from fastapi import FastAPI, HTTPException, Body, Request
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi_mcp import FastApiMCP
# from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import asyncpg
from openai import OpenAI
import re
import json
from presidio.processor import PresidioProcessor
from pathlib import Path

# Global processor cache
processor_cache = {}

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

class NLtoSQLRequest(BaseModel):
    prompt: str
    # schema: str = ""

class ChatMessage(BaseModel):
    message: str


class ValidateSQLRequest(BaseModel):
    query: str
    schema: str

class ExecuteSQLRequest(BaseModel):
    valid: bool
    error: str | None = None
    query: str


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
    
    for model_family, model_name, threshold in configs:
        try:
            print(f"Creating {model_family} processor with {model_name}")
            get_processor(model_family, model_name, threshold)
        except Exception as e:
            print(f"Warning: Failed to create {model_family}/{model_name} processor: {e}")
    
    print("Processors pre-loaded successfully")
    yield
    
    # Shutdown cleanup
    processor_cache.clear()

app = FastAPI(title="Presidio PII Anonymization Service", lifespan=lifespan)

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


@app.post("/get_postgres_schema", operation_id="get_postgres_schema")
async def get_postgres_schema(connection_str='postgresql://postgres:postgres@postgres-flows:5432/postgres'):
    print(f"Intentando conectar a: {connection_str}")
    schema = {}

    query_columns = """
    SELECT
        table_name,
        column_name,
        data_type
    FROM
        information_schema.columns
    WHERE
        table_schema = 'public'
    ORDER BY
        table_name, ordinal_position;
    """

    try:
        conn = await asyncpg.connect(connection_str)
        print("Conexión exitosa")
    except Exception as e:
        print(f"Error de conexión: {e}")
        return {}

    try:
        rows = await conn.fetch(query_columns)
        for row in rows:
            table_name = row['table_name']
            column_name = row['column_name']
            data_type = row['data_type']

            if table_name not in schema:
                schema[table_name] = []

            schema[table_name].append({
                "column": column_name,
                "type": data_type
            })

        # Filtrar tablas que no tienen registros
        tables_to_remove = []
        for table in schema.keys():
            count_query = f'SELECT COUNT(*) FROM "{table}"'
            count = await conn.fetchval(count_query)
            if count == 0:
                tables_to_remove.append(table)

        if tables_to_remove:
            print("Tablas sin registros y que se dejarán fuera del esquema:")
            for table in tables_to_remove:
                print(f"- {table}")

        # Eliminar tablas sin registros
        for table in tables_to_remove:
            schema.pop(table)

    finally:
        await conn.close()
    
    print(f"Tipo del esquema generado: {type(schema)}")
    return schema

@app.post("/generate_sql")
async def generate_sql(request: ChatMessage):
    prompt_text = request.message
    
    try:
        prompt = f"""
            You are an expert data analyst in SQL generator . Your mission is to translate queries in natural language into high-quality executable SQL.

            {prompt_text}

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
            "x_column": exact same name given to the x column on the generated query,
            "y_column": exact same name given to the y column on the generated query, if we have more than one y column, separate the titles with commas.
           }}
        """
        print('promots')
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


def extract_identifiers(query: str):
    print('Extract identifiers for query:', query)
    tables = set(re.findall(r'FROM\s+([a-zA-Z_][\w]*)', query, re.IGNORECASE))
    tables.update(re.findall(r'JOIN\s+([a-zA-Z_][\w]*)', query, re.IGNORECASE))
    columns = set(re.findall(r'SELECT\s+(.*?)\s+FROM', query, re.IGNORECASE))

    column_parts = []
    for col_block in columns:
        for col in col_block.split(","):
            col = col.strip()

            # Omitir funciones SQL como COUNT(*), SUM(col), etc.
            if re.match(r'^\s*\w+\s*\(.*\)', col):  # detecta funciones SQL
                continue

            col = re.sub(r'\(.*?\)', '', col)  # eliminar funciones si no se omitió antes
            col = col.split(" as ")[0].split(".")[-1].strip()
            if col and col != "*":
                column_parts.append(col)

    return tables, set(column_parts)

def extract_tables_and_columns_from_json(schema_text):
    try:
        # Convertimos string JSON a diccionario
        schema = json.loads(schema_text)
    except json.JSONDecodeError as e:
        print("Error parseando JSON:", e)
        return set(), set()

    all_tables = set()
    all_columns = set()

    # Recorremos las tablas
    for table in schema.get("tables", []):
        table_name = table.get("name")
        if table_name:
            all_tables.add(table_name)
        # Recorremos las columnas de cada tabla
        for column in table.get("columns", []):
            column_name = column.get("name")
            if column_name:
                all_columns.add(column_name)
    print(all_tables)
    print('all_tables')
    return all_tables, all_columns

def clean_column_identifiers(columns_used):
    cleaned = set()
    for col in columns_used:
        # Eliminar alias tipo "AS algo"
        col = re.sub(r"\s+AS\s+\w+", "", col, flags=re.IGNORECASE)

        # Eliminar funciones SQL comunes (ej: DATE_TRUNC(...), SUM(...), etc.)
        col = re.sub(r"\b[A-Z_]+\s*\([^)]*\)", "", col, flags=re.IGNORECASE)

        # Quitar comas, paréntesis, y espacios residuales
        col = col.replace(")", "").replace("(", "")
        col = col.replace(",", "").strip().strip('"').strip("'")

        # Ignorar vacíos o palabras reservadas
        if col and col.upper() not in {"DATE_TRUNC", "SUM", "COUNT", "AVG", "MIN", "MAX"}:
            cleaned.add(col)
    return cleaned

@app.post("/validate_sql", operation_id="validate_sql")
async def validate_sql(request: ValidateSQLRequest = Body(...)):
    try:
        print('validacion')
        query = request.query
        schema_text = request.schema
     

        # Extraer identificadores
        tables_used, columns_used = extract_identifiers(query)
        columns_used = clean_column_identifiers(columns_used)
        print('tablas de la query', tables_used)
        print('columns used  de la query_____________________', columns_used)
        # print("tipo de schema_text:", type(schema_text))
        # print("contenido de schema_text:", schema_text)

        print('Se han extraído los identificadores del query')

        all_tables, all_columns = extract_tables_and_columns_from_json(schema_text)
        print('Se han extraído las tablas y columnas del texto schema')
        print('tablas del esquema',all_tables )

        invalid_tables = tables_used - all_tables
        print('invalid tables', invalid_tables)
        invalid_columns = columns_used - all_columns

        if invalid_tables:
            return {"valid": False, "error": f"Tablas no existentes: {', '.join(invalid_tables)}"}
        if invalid_columns:
            return {"valid": False, "error": f"Columnas no existentes: {', '.join(invalid_columns)}"}

        return {"valid": True, "error": None}

    except Exception as e:
        return {"valid": False, "error": f"Error durante validación: {str(e)}"}

@app.post("/saludar")
async def saludar_tool(nombre: str):
    """
    Tool simple que saluda a la persona cuyo nombre se recibe como argumento.
    """
    salida= {"mensaje": f"¡Hola chiribita, {nombre}!"}
    print(salida)
    print(type(salida))
    return salida


@app.post("/execute_sql", operation_id="execute_sql")
async def execute_sql(request: ExecuteSQLRequest = Body(...),  connection_str = 'postgresql://postgres:postgres@postgres-flows:5432/postgres'):
    try:
        # 1️⃣ Validar que la query sea válida
        if not request.valid:
            return {"error": f"Validación fallida: {request.error or 'Error desconocido'}"}

        raw_query = request.query.strip()
        print('received query:', raw_query)

        if not raw_query:
            return {"error": "No se proporcionó una consulta SQL."}

        # Eliminar backticks primero
        cleaned_query = raw_query.replace("```", "").strip()

        # Manejar el caso donde viene con "json" al inicio
        if cleaned_query.lower().startswith("json"):
            # Remover la palabra "json" y limpiar espacios
            cleaned_query = cleaned_query[4:].strip()

        # Extraer SQL si viene como JSON
        if cleaned_query.startswith("{"):
            try:
                query_dict = json.loads(cleaned_query)
                query = query_dict.get("query", "").strip()
                if not query:
                    return {"error": "JSON recibido no contiene la clave 'query' o está vacío."}
            except json.JSONDecodeError as e:
                return {"error": f"JSON inválido en la query: {str(e)}"}
        else:
            query = cleaned_query

        print('query limpia', query)

        # 3️⃣ Conectarse a la base de datos
        connection = await asyncpg.connect(connection_str)

        # 4️⃣ Ejecutar SQL
        if query.lower().startswith("select"):
            rows = await connection.fetch(query)
            result = [dict(row) for row in rows]
        else:
            await connection.execute(query)
            print('esta en la conexion')
            result = "Consulta ejecutada correctamente (sin resultados)"

        # 5️⃣ Cerrar conexión
        await connection.close()
        return {"result": result}

    except Exception as e:
        return {"error": f"Error al ejecutar SQL: {str(e)}"}

# mcp = FastMCP.from_fastapi(app=app)

mcp = FastApiMCP(
    app,
    name="Anonymization MCP",
    description="Anonymization service using Presidio.",
    describe_all_responses=True,
    describe_full_response_schema=True
)
# print(dir(mcp))
# import inspect

# for attr_name in dir(mcp):
#     attr = getattr(mcp, attr_name)
#     if inspect.isfunction(attr) or inspect.ismethod(attr):
#         print(f"--- {attr_name} ---")
#         print(inspect.getdoc(attr))
#         print()

# print('---------------------------')
# print(mcp._describe_all_responses())
# @app.get("/mcp/describe")
# async def mcp_description():
#     return JSONResponse(mcp.describe_all_responses)

mcp.mount()

# print(mcp.tools)


from fastapi.responses import JSONResponse

@app.get("/mcp/describe")
async def mcp_describe():
    tools_json = [tool.dict() for tool in mcp.tools]
    return JSONResponse(tools_json)

from fastapi import Body

@app.post("/mcp")
async def mcp_call_tool(
    tool_name: str = Body(..., embed=True),
    arguments: dict = Body(default_factory=dict),
):
    """
    Ejecuta una tool MCP usando tool_name y argumentos.
    """
    result = await mcp._execute_api_tool(
        client=mcp._http_client,
        tool_name=tool_name,
        arguments=arguments,
        operation_map=mcp.operation_map
    )
    return result

# from fastapi_mcp.transport.sse import FastApiSseTransport

# sse_transport = FastApiSseTransport("/mcp/messages/")

# @app.post("/mcp/messages/")
# async def mcp_messages(request: Request):
#     return await sse_transport.handle_fastapi_post_message(request)