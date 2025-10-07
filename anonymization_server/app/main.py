from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi_mcp import FastApiMCP
import os
import asyncpg
import openai
import re
import json
from presidio.processor import PresidioProcessor 

# Global processor cache
processor_cache = {}

openai.api_key = os.getenv("OPENAI_API_KEY")

class AnonymizationRequest(BaseModel):
    text: str
    model_family: str = "spaCy"
    model_name: str = "en_core_web_lg"
    threshold: float = 0.4

class AnonymizationResponse(BaseModel):
    entities: Optional[List[Dict[str, Any]]] = None

class NLtoSQLRequest(BaseModel):
    prompt: str
    schema: str = ""


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


@app.post("/generate_sql", operation_id="generate_sql")
async def generate_sql(request: NLtoSQLRequest):
    try:
        prompt = f"""
            Eres un experto en SQL (PostgreSQL). Tu tarea es convertir una consulta en lenguaje natural 
            a una instrucción SQL válida, usando EXCLUSIVAMENTE las tablas y columnas que aparecen 
            en el esquema de base de datos proporcionado.

            Reglas importantes:
            1. Usa solo tablas y columnas que estén en el esquema dado.
            2. Respeta la sintaxis de PostgreSQL (ANSI SQL estándar cuando aplique).
            3. No inventes nombres de tablas o columnas.
            4. Devuelve ÚNICAMENTE la consulta SQL, sin comentarios ni explicaciones adicionales.
            5. Si la petición no se puede responder con la información disponible en el esquema,
            devuelve: SELECT 'Not possible';
            6. Optimiza la consulta para claridad y corrección.

            Esquema y consulta en lenguaje natural:
            {request.prompt}

            SQL:
        """

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Eres un experto en SQL y PostgreSQL."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=300
        )

        sql = response["choices"][0]["message"]["content"]
        return {"sql": sql.strip()}

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

# def extract_tables_and_columns_from_json(schema_json: str):
#     """
#     Extrae tablas y columnas desde un esquema en formato JSON
#     """
#     try:
#         schema = json.loads(schema_json)
#         tables = set(schema.keys())  # nombres de tablas
#         columns = set()
#         for table_name, cols in schema.items():
#             for col in cols:
#                 columns.add(col['column'])
#         return tables, columns
#     except Exception as e:
#         print("Error parseando esquema JSON:", e)
#         return set(), set()

# @app.post("/validate_sql", operation_id="validate_sql")
# async def validate_sql(request: ValidateSQLRequest = Body(...)):
#     try:
#         print('validacion')
#         query = request.query
#         schema_text = request.schema
#         print(query)
#         print(schema_text)

#         # Extraer identificadores
#         tables_used, columns_used = extract_identifiers(query)
#         print('tablas de la query', tables_used)

#         print('Se han extraído los identificadores del query')

#         all_tables, all_columns = extract_tables_and_columns_from_json(schema_text)
#         print('Se han extraído las tablas y columnas del texto schema')
#         print('tablas del esquema',all_tables )

#         invalid_tables = tables_used - all_tables
#         print('invalid tables', invalid_tables)
#         invalid_columns = columns_used - all_columns

#         if invalid_tables:
#             return {"valid": False, "error": f"Tablas no existentes: {', '.join(invalid_tables)}"}
#         if invalid_columns:
#             return {"valid": False, "error": f"Columnas no existentes: {', '.join(invalid_columns)}"}

#         return {"valid": True, "error": None}

#     except Exception as e:
#         return {"valid": False, "error": f"Error durante validación: {str(e)}"}

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

@app.post("/validate_sql", operation_id="validate_sql")
async def validate_sql(request: ValidateSQLRequest = Body(...)):
    try:
        print('validacion')
        query = request.query
        schema_text = request.schema
     

        # Extraer identificadores
        tables_used, columns_used = extract_identifiers(query)
        print('tablas de la query', tables_used)
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

mcp = FastApiMCP(
    app,
    name="Anonymization MCP",
    description="Anonymization service using Presidio.",
    describe_all_responses=True,
    describe_full_response_schema=True
)

mcp.mount()
