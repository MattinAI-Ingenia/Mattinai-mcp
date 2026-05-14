from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional, Literal
from fastapi_mcp import FastApiMCP
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import os
import asyncpg
from openai import OpenAI
import re
import json
from presidio.processor import PresidioProcessor
from pathlib import Path
import httpx
import uuid
import logging
from datetime import datetime

# Global processor cache
processor_cache = {}
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
load_dotenv()



api_key = os.getenv("OPENAI_API_KEY")
print(api_key)
client = OpenAI(api_key=api_key)

class AnonymizationRequest(BaseModel):
    text: str
    model_family: str = "spaCy"
    model_name: str = "en_core_web_lg"
    threshold: float = 0.4

class AnonymizationResponse(BaseModel):
    entities: Optional[List[Dict[str, Any]]] = None


class ChatMessage(BaseModel):
    available_datasources: str
    user_question: str

class GenerateSQLRequest(BaseModel):
    
    schema: str
    db_name: str
    db_type: str
    chart_type: str
    user_question: str

class GenerateNoSQLRequest(BaseModel):
    datasource_name: str
    schema: str
    database_name: str
    database_type: str
    chart_type: str
    user_question: str

class Greetings(BaseModel):
    name: str
  
def get_processor(model_family, model_name, threshold):
    # Unique cache key
    cache_key = f"{model_family}_{model_name}_{threshold}"
    
    # Return cached processor if available
    if cache_key in processor_cache:
        return processor_cache[cache_key]
    
    processor = PresidioProcessor(
        model_family=model_family,
        model_name=model_name,
        threshold=threshold
    )
    
    # Cache the processor
    processor_cache[cache_key] = processor
    return processor

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Pre-create all processors
    print("Pre-loading processors...")
    
    # Common model configurations
    configs = [
        ("spaCy", "en_core_web_lg", 0.4),
        ("flair", "ner-english", 0.4),
    ]
    
    yield
    
    # Shutdown cleanup
    processor_cache.clear()

app = FastAPI(title="Presidio PII Anonymization Service", lifespan=lifespan)

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/anonymize", response_model=AnonymizationResponse, operation_id="anonymize_text")
async def anonymize_text(request: AnonymizationRequest):
    try:
        processor = get_processor(
            request.model_family,
            request.model_name,
            request.threshold
        )

        processed_text, entities = processor.process_text(
            text=request.text
        )
        
        response = {"entities": entities}
        
        return response
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing text: {str(e)}")


backend_api = os.getenv("BACKEND_API", "https://lyndsay-semirigorous-raymond.ngrok-free.dev")

class DataSourceRegistrationRequest(BaseModel):
    name: str
    description: str
    type: Literal["postgresql", "mysql", "sqlite", "mongodb"] 
    host: str
    port: int
    database: str
    username: str
    password: str
    schema: Optional[str] = None

@app.post("/register_datasource", operation_id="register_datasource")
async def register_datasource(request: DataSourceRegistrationRequest, user_id: int):
    """
    Register a new data source in the platform.
    Supports PostgreSQL, MySQL, MongoDB, and other database types.
    """
    try:
        # Call your backend API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{backend_api}/api/v1/data-sources/",
                json={
                    "name": request.name,
                    "description": request.description,
                    "type": request.type,
                    "connection": {
                        "host": request.host,
                        "port": request.port,
                        "database": request.database,
                        "username": request.username,
                        "password": request.password
                    }
                },
                params={"user_id": user_id, "schema": request.schema}  # Handle user_id properly
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CreateDashboardRequest(BaseModel):
    user_id: int
    name: str
    description: str

@app.post("/create_dashboard", operation_id="create_dashboard")
async def create_dashboard(request: CreateDashboardRequest):
    """Create a new empty dashboard for the user."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{backend_api}/api/v1/dashboards/save",
            json={
                "user_id": request.user_id,
                "name": request.name,
                "description": request.description,
                "visualizations": []
            }
        )

        sources_response = await client.get(
            f"{backend_api}/api/v1/data-sources/",
            params={"user_id": request.user_id}
        )

        response.raise_for_status()
        return {
                "dashboard": response.json(),
                "available_data_sources": sources_response.json()
            }
    
class AddVisualizationRequest(BaseModel):
    user_id: int
    dashboard_id: int
    query: str  # NLP query like "show sales by region as pie chart"

@app.post("/add_visualization", operation_id="add_visualization")
async def add_visualization(request: AddVisualizationRequest):
    """Generate and add a visualization to an existing dashboard."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Generate viz config
        viz_response = await client.post(
            f"{backend_api}/api/v1/nlp/query",
            json={"query": request.query},
            params={"user_id": request.user_id}
        )
        viz_response.raise_for_status()
        viz_data = viz_response.json()
        
        # Check if successful
        if not viz_data.get("success"):
            raise HTTPException(500, f"Viz generation failed: {viz_data.get('error')}")
        
        # Load dashboard
        dash_response = await client.get(f"{backend_api}/api/v1/dashboards/{request.dashboard_id}")
        dash_response.raise_for_status()
        dashboard = dash_response.json()
        
        # Format visualization for dashboard
        viz_result = viz_data["result"]
        formatted_viz = {
            "id": str(uuid.uuid4()),
            "title": viz_result["title"],
            "description": viz_result.get("description", ""),
            "chart_type": viz_result["chart_type"],
            "data_source": viz_result["data_source"],
            "query": {
                "type": "mongodb" if "mongodb_query" in viz_result else "sql",
                "statement": viz_result.get("mongodb_query") or viz_result.get("sql")
            },
            "original_query": viz_result["original_user_query"],
            "query_config": viz_result["query_config"],
            "config": viz_result["config"],
            "edit_history": [],
            "created_at": datetime.now().isoformat() + "Z"
        }
        
        # Add to dashboard
        dashboard_data = dashboard["dashboard_data"]
        dashboard["dashboard_data"]["visualizations"].append(formatted_viz)
        dashboard_data["id"] = request.dashboard_id
        dashboard["dashboard_data"]["user_id"] = request.user_id
        
        # Save
        save_response = await client.post(
            f"{backend_api}/api/v1/dashboards/save",
            json=dashboard_data
        )
        save_response.raise_for_status()
        return {
            **save_response.json(),
            "action_taken": "visualization_added" 
        }
    
class EditVisualizationRequest(BaseModel):
    user_id: int
    dashboard_id: int
    visualization_id: str
    edit_instructions: str

@app.post("/edit_dashboard_visualization", operation_id="edit_dashboard_visualization")
async def edit_dashboard_visualization(request: EditVisualizationRequest):
    """Edit a specific visualization in an open dashboard."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Load dashboard
        dash_response = await client.get(f"{backend_api}/api/v1/dashboards/{request.dashboard_id}")
        dash_response.raise_for_status()
        dashboard = dash_response.json()
        
        # Find the visualization
        viz_index = next((i for i, v in enumerate(dashboard["dashboard_data"]["visualizations"]) 
                         if v["id"] == request.visualization_id), None)
        
        if viz_index is None:
            raise HTTPException(404, "Visualization not found")
        
        original_viz = dashboard["dashboard_data"]["visualizations"][viz_index]
        
        # ⭐ Transform to format expected by /nlp/query/edit
        edit_payload = {
            "original_visualization": {
                "title": original_viz["title"],
                "description": original_viz.get("description", ""),
                "chart_type": original_viz["chart_type"],
                "data_source": original_viz["data_source"],
                "original_user_query": original_viz.get("original_query", original_viz.get("original_user_query", "")),  # ⭐ Map correctly
                "sql": original_viz.get("query", {}).get("statement") if original_viz.get("query", {}).get("type") == "sql" else None,
                "mongodb_query": original_viz.get("query", {}).get("statement") if original_viz.get("query", {}).get("type") == "mongodb" else None,
                "query_config": original_viz.get("query_config", {}),
                "config": original_viz.get("config", {}),
                "edit_history": original_viz.get("edit_history", [])
            },
            "edit_instructions": request.edit_instructions
        }
        
        # Call edit endpoint
        edit_response = await client.post(
            f"{backend_api}/api/v1/nlp/query/edit",
            json=edit_payload
        )
        edit_response.raise_for_status()
        edited = edit_response.json()
        
        # ⭐ Transform result back to dashboard format
        edited_result = edited["result"]
        updated_viz = {
            **original_viz,  # Keep id, created_at, etc.
            "title": edited_result["title"],
            "description": edited_result.get("description", ""),
            "chart_type": edited_result["chart_type"],
            "query": {
                "type": "mongodb" if "mongodb_query" in edited_result else "sql",
                "statement": edited_result.get("mongodb_query") or edited_result.get("sql")
            },
            "query_config": edited_result["query_config"],
            "config": edited_result.get("config", {}),
            "edit_history": edited_result.get("edit_history", []),
            "created_at": original_viz.get("created_at", datetime.now().isoformat() + "Z")
        }
        
        dashboard["dashboard_data"]["visualizations"][viz_index] = updated_viz
        
        dashboard["dashboard_data"]["user_id"] = request.user_id
        dashboard["dashboard_data"]["id"] = request.dashboard_id

        logger.info(f"Saving dashboard with viz: {updated_viz}")
        logger.info(f"Full dashboard payload: {json.dumps(dashboard['dashboard_data'], indent=2)}")

        # Save
        save_response = await client.post(
            f"{backend_api}/api/v1/dashboards/save", 
            json=dashboard["dashboard_data"]
        )

        return {
            **save_response.json(),
            "action_taken": "visualization_edited" 
        }
    
@app.post("/saludar")
async def saludar_tool(request: Greetings):
    """
    Tool simple que saluda a la persona cuyo nombre se recibe como argumento.
    """
    print('saludo')
    salida= {"mensaje": f"¡Egun on, {request.name}!"}
    
    print(type(salida))
    return salida


@app.post("/select_datasource", operation_id="select_datasource")
async def select_datasource(request: ChatMessage = Body(...)):
    print("select_datasource")
    try:
        prompt = f"""
            You are a database selection assistant. Your only task is to choose the most appropriate database from a given list based on the user's question. 
            User's question: {request.user_question}
            Available databases:
            {request.available_datasources}
            ##RULES:
            1. Only choose **one database**. Do not provide SQL, queries, or explanations.
            2. The database must match the context and needs implied by the user's question.
            3. Always respond with the **exact name** of the database from the provided list. No extra text.
            4. If multiple databases seem suitable, choose the one that fits **best**.
            5. database type should be mongodb or postgresql

            ##OUTPUT FORMAT:
            Always respond with the following json:
            {{
            "database_name": choosen database name (from the list)
            "database_type": choosen database type
            "explanation": brief explanation why you choose that database
            }}

        """
                    # 
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Eres un experto en generar SQL y PostgreSQL."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=300
        )
        print('response', response)

        datasource_response = response.choices[0].message.content
        print(datasource_response)
        return {"response":f"{datasource_response}"}

    except Exception as e:
        return {"error": str(e)}


@app.post("/generate_nosql", operation_id="generate_nosql")
async def generate_nosql(request: GenerateNoSQLRequest = Body(...)):

    try:
        prompt = f"""
            You are an expert data analyst in MongoDB query generator. Your mission is to translate queries in natural language into high-quality executable MongoDB aggregation pipelines.

            ### Datasource name:
            {request.datasource_name}

            ### AVAILABLE SCHEMA:
            {request.schema}

            ### Database name:
            {request.database_name}

            ### Database type:
            {request.database_type}

            ### visualization type:
            {request.chart_type}

            ### USER QUERY:
            {request.user_question}

            ##RULES
            1. **Only** use collections and fields that are present in the database schema.
            2. **Always** use the collection name exactly as defined in the schema.
            3. **Always** use lowercase when defining field aliases in $project stages.
            4. Build optimized pipelines for large datasets:
            5. For grouped data, use $group with _id for grouping fields and accumulators for calculations.
            6. Define x and y column titles based on the field aliases in $project stage.
            7. In $group stages, always reference original field names with $ prefix (e.g., $fieldName).

            ##SPECIFIC VISUALIZATION RULES
            1. For time series with multiple categories, use $group followed by $project to create separate fields for each category.
            2. Structure output documents with clear field names suitable for charting (x-axis and y-axis values).

            ##RESPONSE FORMAT
            Always respond with this JSON structure:
            {{
            "collection": "collection_name",
            "pipeline": [MongoDB aggregation pipeline array],
            "database_name": name of the database where the query will be executed,
            "database_type": type of the database where the query will be executed,
            "x_column": "exact field name for x-axis in final output",
            "y_column": "exact field name(s) for y-axis, comma-separated if multiple"
            }}
        """
        print('PROMPT GENERADO:')
        print(prompt)
        print()
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Eres un experto en generar NoSQL y Mongodb."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=300
        )
        print('response', response)

        sql_response = response.choices[0].message.content
        print(sql_response)
        return {"response":f"{sql_response}"}

    except Exception as e:
        return {"error": str(e)}
             

@app.post("/generate_sql", operation_id="generate_sql")
async def generate_sql(request: GenerateSQLRequest = Body(...)):
  
    try:
        prompt = f"""
            You are an expert data analyst in SQL generator . Your mission is to translate queries in natural language into high-quality executable SQL.

            ### AVAILABLE SCHEMA:
            {request.schema}

            ### Database name:
            {request.db_name}

            ### Database type:
            {request.db_type}

            ### visualization type:
            {request.chart_type}

            ### USER QUERY:
            {request.user_question}

            ##RULES
            1. **Only** use columns and tables that are present in the databse schema.
            2.**Always** use schema_name.table_name to construct the FROM statements. 
            3. **Always** use lowercase when defining sql alias.
            3. Build optimized for execution because we may have a lot of data: 
            - Pre-aggregate large tables in subqueries before joining
            - Avoid Cartesian products from one-to-many joins
            - Use indexes: add WHERE clauses on primary/foreign keys when possible
            - Limit result sets early with WHERE before GROUP BY
            - Use DISTINCT only when necessary
            - Prefer EXISTS over IN for subqueries with large datasets
            4. Define a x column and y column title based on the alias of the generated queries.
            5. If a GROUP BY is needed, **always** use the column name, **never** its alias.
                
            ##SPECIFIC VISUALIZATION RULES
            1. If more than one row is needed in the time series, each category must be in a separate column of the resulting query data frame.

            ##RESPONSE FORMAT
            Always respond with this json structure: 
            {{
            "query": generated sql query,
            "database_name": name of the database where the query will be executed,
            "database_type": type of the database where the query will be executed,
            "x_column": exact same name given to the x column on the generated query,
            "y_column": exact same name given to the y column on the generated query, if we have more than one y column, separate the titles with commas.
            }}
        """
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Eres un experto en generar SQL y PostgreSQL."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=300
        )
        print('response', response)

        sql_response = response.choices[0].message.content
        print(sql_response)
        return {"response":f"{sql_response}"}

    except Exception as e:
        return {"error": str(e)}

mcp = FastApiMCP(
    app,
    name="Anonymization MCP",
    description="Anonymization service using Presidio.",
    describe_all_responses=True,
    describe_full_response_schema=True
)

mcp.mount()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)