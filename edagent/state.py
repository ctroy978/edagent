"""LangGraph State Schema for the multi-agent system."""

from typing import TypedDict, Annotated, Sequence
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """State for the multi-agent routing system with workflow tracking.

    Attributes:
        messages: Conversation history (automatically merged by add_messages)
        next_step: Routing decision ('gather_materials', 'test_grading', 'general', 'email_distribution', or END)
        job_id: Optional job ID for grading operations (passed to email distribution)

        current_phase: Current phase in workflow ('gather', 'prepare', 'inspect', 'evaluate', 'report')

        rubric_text: Grading rubric text (gathered in gather_materials phase)
        question_text: Essay question/prompt text (optional, gathered in gather_materials phase)
        knowledge_base_topic: Topic name if reading materials were added to knowledge base (populated in gather_materials phase)
        context_material: Retrieved context from knowledge base (populated in evaluate phase)

        materials_added_to_kb: Flag indicating if reading materials were added to knowledge base
        ocr_complete: Flag indicating if OCR processing is complete
        scrubbing_complete: Flag indicating if PII scrubbing is complete
        evaluation_complete: Flag indicating if evaluation is complete

        clean_directory_path: Path to cleaned/prepared directory from prepare_files_for_grading
        student_count: Expected number of students (for verification)
        essay_format: Essay format type ('handwritten' or 'typed')
    """

    # --- Existing fields (keep) ---
    messages: Annotated[Sequence[BaseMessage], add_messages]
    next_step: str
    job_id: str | None

    # --- NEW: Workflow tracking ---
    current_phase: str | None

    # --- NEW: Gathered materials ---
    rubric_text: str | None
    question_text: str | None
    knowledge_base_topic: str | None
    context_material: str | None

    # --- NEW: Workflow progress flags ---
    materials_added_to_kb: bool
    ocr_complete: bool
    scrubbing_complete: bool
    evaluation_complete: bool

    # --- NEW: Processing metadata ---
    clean_directory_path: str | None
    student_count: int | None
    essay_format: str | None
