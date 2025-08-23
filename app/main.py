from typing import Any, Dict, Union
from fastapi import FastAPI, HTTPException
from fastapi.params import Body
from fastapi.responses import JSONResponse
from app.schemas import SimulationRequest, SimpleSimulationRequest, HealthResponse
from app.agent import generate_simulation, generate_simulation_simple
from fastapi.responses import FileResponse
from pathlib import Path

RequestBody = Union[SimpleSimulationRequest, SimulationRequest]

app = FastAPI(
    title="ChronoScribe Agent",
    description="What-if simulator: explores alternate timelines with branching scenarios.",
    version="1.2.0",
)

@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    return HealthResponse(status="ok")

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.post("/simulate", response_model=Dict[str, Any], tags=["simulate"])
def simulate(
    body: RequestBody = Body(
        ...,
        examples={
            "simple_minimal": {
                "summary": "Minimal (most users)",
                "value": {"what_if": "What if the printing press was never invented?"}
            },
            "simple_with_vibe": {
                "summary": "Simple + vibe",
                "value": {
                    "what_if": "What if commercial fusion wins in 2030?",
                    "preset": "cinematic",
                    "horizon": "long",
                    "focus": "tech"
                }
            },
            "advanced_full": {
                "summary": "Advanced controls (power user)",
                "value": {
                    "what_if": "What if the printing press was never invented?",
                    "time_horizon": "50y",
                    "scope": "mixed",
                    "style": "brief",
                    "constraints": ["stay physically plausible"],
                    "temperature": 0.7
                }
            },
        },
    )
):
    try:
        if isinstance(body, SimpleSimulationRequest):
            result = generate_simulation_simple(body)
        else:
            result = generate_simulation(body)
        return JSONResponse(content=result)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
