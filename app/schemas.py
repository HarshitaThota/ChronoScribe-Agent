from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class SimulationRequest(BaseModel):
    what_if: str = Field(..., example="What if the printing press was never invented?")
    time_horizon: str = Field(default="50y")
    scope: str = Field(default="mixed")
    style: Optional[str] = Field(default="brief")
    constraints: Optional[List[str]] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)

class HealthResponse(BaseModel):
    status: str

# 👇 New: super-simple input
class SimpleSimulationRequest(BaseModel):
    what_if: str = Field(..., example="What if the printing press was never invented?")
    # few friendly knobs (optional)
    preset: Literal["default","cinematic","academic","risk","optimistic","pessimistic"] = "default"
    horizon: Literal["short","medium","long"] = "long"   # short=5y, medium=25y, long=50y
    focus: Literal["mixed","tech","history","economics","culture","geopolitics","science"] = "mixed"
