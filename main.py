from fastapi import FastAPI, UploadFile, File

app = FastAPI(title="AI OCR Service", version="0.1.0")

@app.post("/ocr/extract")
async def extract_text(file: UploadFile = File(...)):
    """
    Extracts text from a PDF file using Typhoon OCR.
    (Mocked endpoint for MCP testing)
    """
    # Wait for the file to be uploaded/read just to ensure functionality
    content = await file.read()
    
    return {
        "text": "Successfully extracted text (Mocked for MCP testing)."
    }

@app.get("/")
def health_check():
    """Health Check"""
    return {"status": "ok"}
