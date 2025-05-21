import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi_mcp import FastApiMCP

from presidio.processor import PresidioProcessor 

# Global processor cache
processor_cache = {}

class AnonymizationRequest(BaseModel):
    text: str
    model_family: str = "spaCy"
    model_name: str = "en_core_web_lg"
    threshold: float = 0.4
    allow_list: Optional[List[str]] = None
    deny_list: Optional[List[str]] = None

class AnonymizationResponse(BaseModel):
    entities: Optional[List[Dict[str, Any]]] = None

def get_processor(model_family, model_name, threshold, allow_list, deny_list):
    # Unique cache key
    cache_key = f"{model_family}_{model_name}_{threshold}"
    
    # Return cached processor if available
    if cache_key in processor_cache:
        return processor_cache[cache_key]
    
    processor = PresidioProcessor(
        model_family=model_family,
        model_name=model_name,
        threshold=threshold,
        allow_list=allow_list or [],
        deny_list=deny_list or []
    )
    
    # Cache the processor
    processor_cache[cache_key] = processor
    return processor

app = FastAPI(title="Presidio PII Anonymization Service")

@app.post("/anonymize", response_model=AnonymizationResponse, operation_id="anonymize_text")
async def anonymize_text(request: AnonymizationRequest):
    try:
        processor = get_processor(
            request.model_family,
            request.model_name,
            request.threshold,
            request.allow_list,
            request.deny_list
        )

        processed_text, entities = processor.process_text(
            text=request.text
        )
        
        response = {"entities": entities}
        
        return response
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing text: {str(e)}")

mcp = FastApiMCP(
    app,
    name="Anonymization MCP",
    description="Anonymization service using Presidio.",
    describe_all_responses=True,
    describe_full_response_schema=True
)

mcp.mount()
