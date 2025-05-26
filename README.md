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
