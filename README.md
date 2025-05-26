# Mattinai-mcp

This service provides two main endpoints:
1. **Text Anonymization** - Detects entities in text.
2. **Model Context Protocol (MCP)** - Expose endpoints as tools for LLM.

## Deploy

Clone the repository

```bash
git clone https://github.com/MattinAI/Mattinai-mcp.git
```

Enter the folder ans start the service:

```bash
cd anonymization_server
```

```bash
docker compose up --build
``` 

## API Endpoints 

### 1. Text Anonymization
`POST /anonymize`

**Parameters**

- model_family: Model family to use ("spacy", "flair")

- model_name: 

    - Spacy: en_core_web_lg
    - Flair: ner-english

- threshold: Confidence threshold (0-1)

### 2. MCP (Model Context Protocol)
`GET /mcp`

Exposes service endpoints as MCP tools for LLM integration. Returns OpenAPI schema compatible with MCP clients, enabling LLMs to discover and use the anonymization service programmatically.

To use it you have to provide the service url to the MCP server, for example:

```
http://<container_name>:<PORT>/mcp
```