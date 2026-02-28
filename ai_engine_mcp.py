"""
AI Engine MCP Server
--------------------
Exposes the AI Engine FastAPI service as MCP tools.

Base URL: http://165.232.171.127

Endpoints covered:
  POST /ocr/extract                      -> extract_text_ocr
  POST /sheets/analyze                   -> analyze_sheet
  GET  /sheets/jobs/{job_id}             -> get_job_status
  GET  /sheets/jobs/by-sheet/{sheet_id}  -> get_job_by_sheet_id
  POST /api/audio/transcribe             -> transcribe_audio
  POST /api/chat/                        -> chat_with_rag

IMPORTANT — sheet_id disambiguation:
  • AnalyzeJob.sheet_id  : optional string you pass to /sheets/analyze to tag a job.
                           Used to look up the job later via /sheets/jobs/by-sheet/{sheet_id}.
  • AiDatasetRecord.id   : integer primary key of a saved AI dataset record.
                           This is the value that /api/chat/ accepts as `sheet_id`
                           to inject RAG context. Audio transcription jobs save their
                           result to AiDatasetRecord and expose `dataset_id` in the
                           completed job result.
  • PDF analysis jobs    : do NOT create an AiDatasetRecord — their content lives
                           only inside AnalyzeJob.result. RAG chat is therefore only
                           available for transcribed audio (or other records in the DB).
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------

mcp = FastMCP("AIEngine")
BASE_URL = "http://165.232.171.127"

# Default poll interval and maximum wait time (seconds)
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_job(job: dict) -> str:
    """Return a clean, human-readable summary of a job status response."""
    status = job.get("status", "unknown")
    job_id = job.get("job_id", "n/a")
    error = job.get("error_message")
    result = job.get("result")

    lines = [
        f"job_id : {job_id}",
        f"status : {status}",
    ]
    if job.get("sheet_id"):
        lines.append(f"sheet_id (tag): {job['sheet_id']}")
    if job.get("created_at"):
        lines.append(f"created_at : {job['created_at']}")
    if error:
        lines.append(f"error : {error}")
    if result:
        lines.append("\n--- result ---")
        lines.append(json.dumps(result, indent=2, ensure_ascii=False))

    return "\n".join(lines)


async def _poll_job(client: httpx.AsyncClient, job_id: str) -> dict:
    """
    Poll GET /sheets/jobs/{job_id} until the job reaches a terminal state
    (completed or failed) or the timeout is exceeded.

    Returns the final job dict on success or raises an Exception on failure /
    timeout.
    """
    deadline = time.monotonic() + _POLL_TIMEOUT

    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Timed out after {_POLL_TIMEOUT}s waiting for job {job_id}."
            )

        resp = await client.get(f"{BASE_URL}/sheets/jobs/{job_id}")
        resp.raise_for_status()
        job = resp.json()

        status = job.get("status", "").lower()
        if status == "completed":
            return job
        if status == "failed":
            raise RuntimeError(
                f"Job {job_id} failed. Error: {job.get('error_message', 'unknown')}"
            )

        await _async_sleep(_POLL_INTERVAL)


async def _async_sleep(seconds: float) -> None:
    """Async sleep without importing asyncio at module level."""
    import asyncio
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Tool 1 – OCR text extraction
# ---------------------------------------------------------------------------

@mcp.tool()
async def extract_text_ocr(file_path: str) -> str:
    """
    Extract raw text from a PDF file using the AI Engine OCR service.

    Sends the PDF to POST /ocr/extract and returns the extracted text string.

    Args:
        file_path: Absolute path to the PDF file on the local filesystem.

    Returns:
        Extracted text as a plain string, or an error message.
    """
    if not os.path.exists(file_path):
        return f"Error: File not found at '{file_path}'"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, "application/pdf")}
                response = await client.post(f"{BASE_URL}/ocr/extract", files=files)

            response.raise_for_status()
            result = response.json()
            # Endpoint returns {"text": <extracted text>}
            text = result.get("text")
            if text is None:
                return f"Unexpected response (missing 'text' key): {result}"
            return str(text)

    except httpx.ConnectError:
        return (
            f"Error: Could not connect to the AI Engine at {BASE_URL}. "
            "Is the server running?"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"Error: AI Engine returned HTTP {e.response.status_code}: "
            f"{e.response.text}"
        )
    except Exception as e:
        return f"Unexpected error during OCR extraction: {e}"


# ---------------------------------------------------------------------------
# Tool 2 – Async sheet analysis (PDF → OCR → AI → Job)
# ---------------------------------------------------------------------------

@mcp.tool()
async def analyze_sheet(
    file_path: Optional[str] = None,
    file_url: Optional[str] = None,
    sheet_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> str:
    """
    Submit a PDF for analysis and wait for the result.

    Calls POST /sheets/analyze (202 Accepted), then polls
    GET /sheets/jobs/{job_id} until the job completes or fails.

    Exactly one of `file_path` or `file_url` must be provided.

    Pipeline: Upload → OCR → AI Analysis (summary / assessment / tags) → DB

    NOTE: This endpoint stores results in AnalyzeJob.result (JSON), NOT in
    AiDatasetRecord. Therefore the resulting content is NOT directly queryable
    via RAG chat. Use `transcribe_audio` for audio files if you need RAG chat.

    Args:
        file_path  : Absolute local path to a PDF file.
        file_url   : Public URL to a PDF (http/https). Used if file_path is None.
        sheet_id   : Optional string tag to attach to this job so you can look
                     it up later with get_job_by_sheet_id().
        webhook_url: Optional URL to call when the job completes.

    Returns:
        Human-readable analysis result including summary, assessment points,
        tags, and page count.
    """
    if not file_path and not file_url:
        return "Error: Provide either 'file_path' or 'file_url'."
    if file_path and not os.path.exists(file_path):
        return f"Error: File not found at '{file_path}'"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # --- Step 1: submit the job ---
            form_data: dict = {}
            if file_url:
                form_data["file_url"] = file_url
            if sheet_id:
                form_data["sheet_id"] = sheet_id
            if webhook_url:
                form_data["webhook_url"] = webhook_url

            if file_path:
                with open(file_path, "rb") as f:
                    files = {
                        "file": (os.path.basename(file_path), f, "application/pdf")
                    }
                    submit_resp = await client.post(
                        f"{BASE_URL}/sheets/analyze",
                        files=files,
                        data=form_data,
                    )
            else:
                # URL-only submission (no binary file part needed)
                submit_resp = await client.post(
                    f"{BASE_URL}/sheets/analyze",
                    data=form_data,
                )

            # 202 is the normal success status for this endpoint
            if submit_resp.status_code not in (200, 202):
                submit_resp.raise_for_status()

            submit_result = submit_resp.json()
            job_id = submit_result.get("job_id")
            if not job_id:
                return f"Error: No 'job_id' in submit response: {submit_result}"

            # --- Step 2: poll for completion ---
            job = await _poll_job(client, str(job_id))

        # --- Step 3: format result ---
        result = job.get("result") or {}
        lines = [
            f"✅ Analysis complete  (job_id: {job.get('job_id')})",
            f"Sheet tag (sheet_id): {job.get('sheet_id') or 'n/a'}",
            f"Pages       : {result.get('page_count', 'n/a')}",
            "",
            "── Summary ──",
            result.get("summary", "No summary available."),
            "",
            "── Assessment Points ──",
        ]
        for point in result.get("assessment", []):
            lines.append(f"  • {point}")

        tags = result.get("tags", [])
        if tags:
            lines.append("")
            lines.append("── Tags ──")
            lines.append("  " + "  ".join(tags))

        return "\n".join(lines)

    except httpx.ConnectError:
        return (
            f"Error: Could not connect to the AI Engine at {BASE_URL}. "
            "Is the server running?"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"Error: AI Engine returned HTTP {e.response.status_code}: "
            f"{e.response.text}"
        )
    except (TimeoutError, RuntimeError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Unexpected error during sheet analysis: {e}"


# ---------------------------------------------------------------------------
# Tool 3 – Get job status by job_id
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """
    Check the current status of an analysis or transcription job.

    Calls GET /sheets/jobs/{job_id}.

    Job statuses: pending → processing → completed | failed

    Args:
        job_id: UUID of the job (returned by analyze_sheet or transcribe_audio).

    Returns:
        Formatted job status including result / error if available.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{BASE_URL}/sheets/jobs/{job_id}")
            if resp.status_code == 404:
                return f"Error: Job '{job_id}' not found."
            resp.raise_for_status()
            return _fmt_job(resp.json())

    except httpx.ConnectError:
        return (
            f"Error: Could not connect to the AI Engine at {BASE_URL}. "
            "Is the server running?"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"Error: AI Engine returned HTTP {e.response.status_code}: "
            f"{e.response.text}"
        )
    except Exception as e:
        return f"Unexpected error fetching job status: {e}"


# ---------------------------------------------------------------------------
# Tool 4 – Get job by user-defined sheet_id tag
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_job_by_sheet_id(sheet_id: str) -> str:
    """
    Look up the most recent analysis job associated with a user-defined sheet_id tag.

    Calls GET /sheets/jobs/by-sheet/{sheet_id}.

    This is useful when you submitted an analyze_sheet job with a custom
    `sheet_id` tag and want to retrieve its result without storing the job_id.

    Args:
        sheet_id: The string tag you passed as `sheet_id` when submitting the job.

    Returns:
        Formatted job status and result, or a 404 error if no matching job exists.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{BASE_URL}/sheets/jobs/by-sheet/{sheet_id}")
            if resp.status_code == 404:
                return f"Error: No job found for sheet_id tag '{sheet_id}'."
            resp.raise_for_status()
            return _fmt_job(resp.json())

    except httpx.ConnectError:
        return (
            f"Error: Could not connect to the AI Engine at {BASE_URL}. "
            "Is the server running?"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"Error: AI Engine returned HTTP {e.response.status_code}: "
            f"{e.response.text}"
        )
    except Exception as e:
        return f"Unexpected error fetching job by sheet_id: {e}"


# ---------------------------------------------------------------------------
# Tool 5 – Audio transcription
# ---------------------------------------------------------------------------

@mcp.tool()
async def transcribe_audio(file_path: str) -> str:
    """
    Upload an audio file for background transcription and summarisation,
    then wait for the result.

    Calls POST /api/audio/transcribe, then polls GET /sheets/jobs/{job_id}
    until completion.

    Supported formats: .wav, .mp3, .m4a, .ogg, .flac
    ffmpeg is used server-side to convert to optimised mono MP3 before
    transcription.

    When completed the job result contains:
      - dataset_id              : AiDatasetRecord.id saved to the database.
                                  ⚡ Use this integer as `sheet_id` in
                                  chat_with_rag to enable RAG context.
      - summary                 : AI-generated lecture summary.
      - raw_text_snippet        : First 200 chars of the transcript.
      - full_text_saved_in_dataset: True if the full transcript was stored.

    Args:
        file_path: Absolute local path to the audio file.

    Returns:
        Summary and transcript snippet, plus the dataset_id for RAG chat.
    """
    if not os.path.exists(file_path):
        return f"Error: File not found at '{file_path}'"

    ext = os.path.splitext(file_path)[1].lower()
    allowed = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
    if ext not in allowed:
        return f"Error: Unsupported format '{ext}'. Allowed: {sorted(allowed)}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # --- Step 1: upload ---
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f)}
                submit_resp = await client.post(
                    f"{BASE_URL}/api/audio/transcribe", files=files
                )
            submit_resp.raise_for_status()
            submit_result = submit_resp.json()
            job_id = submit_result.get("job_id")
            if not job_id:
                return f"Error: No 'job_id' in submit response: {submit_result}"

            # --- Step 2: poll ---
            job = await _poll_job(client, job_id)

        # --- Step 3: format result ---
        result = job.get("result") or {}
        dataset_id = result.get("dataset_id", "n/a")
        summary = result.get("summary", "No summary available.")
        snippet = result.get("raw_text_snippet", "")
        saved = result.get("full_text_saved_in_dataset", False)

        lines = [
            f"✅ Transcription complete  (job_id: {job.get('job_id')})",
            f"dataset_id (for RAG chat): {dataset_id}",
            f"Full text saved to DB    : {saved}",
            "",
            "── Summary ──",
            summary,
        ]
        if snippet:
            lines += ["", "── Transcript snippet (first 200 chars) ──", snippet]

        return "\n".join(lines)

    except httpx.ConnectError:
        return (
            f"Error: Could not connect to the AI Engine at {BASE_URL}. "
            "Is the server running?"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"Error: AI Engine returned HTTP {e.response.status_code}: "
            f"{e.response.text}"
        )
    except (TimeoutError, RuntimeError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Unexpected error during transcription: {e}"


# ---------------------------------------------------------------------------
# Tool 6 – RAG-enabled chat
# ---------------------------------------------------------------------------

@mcp.tool()
async def chat_with_rag(
    message: str,
    session_id: str,
    sheet_id: Optional[str] = None,
) -> str:
    """
    Send a message to the AI chat endpoint, optionally with RAG context from
    a saved study-guide sheet.

    Calls POST /api/chat/ (rate-limited to 10 requests/minute per IP).

    ⚡ IMPORTANT — what `sheet_id` means here:
        This must be the INTEGER id of an AiDatasetRecord row — the same value
        returned as `dataset_id` in the transcribe_audio result.
        It is NOT the UUID job_id from analyze_sheet, and it is NOT the string
        tag you assigned as `sheet_id` when calling analyze_sheet.

        When sheet_id is provided:
          - The server fetches the matching AiDatasetRecord.raw_text.
          - The AI acts as a personal tutor for that specific sheet's content.
          - Semantic caching is enabled (Supabase vector store).
        When sheet_id is None:
          - The AI acts as a general sales assistant recommending sheets from
            the marketplace.

    Response fields returned by the server:
        session_id : echoed back
        message    : the AI's reply text
        sheet_id   : echoed back
        logs       : {"cache_hit": bool, "model": str} or {"error": bool}

    Args:
        message   : The user's message / question.
        session_id: Unique conversation session ID (reuse across turns to keep
                    chat history).
        sheet_id  : Optional integer ID of an AiDatasetRecord for RAG context.
                    Pass as a string, e.g. "42".

    Returns:
        The AI's reply, plus cache/model metadata.
    """
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload: dict = {
                "session_id": session_id,
                "message": message,
            }
            if sheet_id is not None:
                payload["sheet_id"] = sheet_id

            response = await client.post(f"{BASE_URL}/api/chat/", json=payload)
            response.raise_for_status()

        result = response.json()

        # The endpoint always returns {"session_id", "message", "sheet_id", "logs"}
        ai_message = result.get("message", "")
        logs = result.get("logs", {})

        lines = []
        if not ai_message:
            lines.append("(AI returned an empty response)")
        else:
            lines.append(ai_message)

        # Append lightweight metadata
        meta_parts = []
        if logs.get("cache_hit"):
            meta_parts.append("cache_hit=true")
        if logs.get("model"):
            meta_parts.append(f"model={logs['model']}")
        if logs.get("error"):
            meta_parts.append("⚠ server reported an error in logs")
        if meta_parts:
            lines.append("")
            lines.append(f"[{' | '.join(meta_parts)}]")

        return "\n".join(lines)

    except httpx.ConnectError:
        return (
            f"Error: Could not connect to the AI Engine at {BASE_URL}. "
            "Is the server running?"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"Error: AI Engine returned HTTP {e.response.status_code}: "
            f"{e.response.text}"
        )
    except Exception as e:
        return f"Unexpected error during chat: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = os.environ.get("PORT")
    if port:
        # Cloud Mode (Heroku SSE)
        print(f"Starting MCP in SSE mode on port {port}")
        mcp.run(transport='sse', host="0.0.0.0", port=int(port))
    else:
        # Local Mode (Cursor Stdio)
        mcp.run()
