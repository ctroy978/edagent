# Email Distribution Integration Guide

This document explains how to integrate email distribution into LangGraph workflows.

---

## Overview

The email distribution system allows any grading workflow to send student feedback reports via email. It uses a **router-based architecture** with state management to pass job information between nodes.

### Key Components

1. **Grading Nodes** (`essay_grading_node`, `test_grading_node`, etc.)
2. **Router Node** (`router_node`) - Routes between workflow stages
3. **Email Distribution Node** (`email_distribution_node`) - Sends emails
4. **State Management** - Passes `job_id` between nodes

---

## Architecture Pattern

### Flow Diagram

```
┌─────────────────┐
│  Grading Node   │
│                 │
│ 1. Complete     │
│    grading      │
│                 │
│ 2. Download     │
│    reports      │
│                 │
│ 3. Ask teacher: │
│    "Email?"     │
│                 │
│ 4. Call tool:   │
│    complete_    │
│    grading_     │
│    workflow()   │
│    WITH job_id  │
└────────┬────────┘
         │
         │ Returns state with:
         │ • job_id: "job_..."
         │ • next_step: "END"
         │
         ▼
┌─────────────────┐
│  Router Node    │
│                 │
│ 1. Check state  │
│    for job_id   │
│                 │
│ 2. Detect email │
│    keywords in  │
│    user message │
│                 │
│ 3. If job_id +  │
│    email intent │
│    → route to   │
│    email_dist   │
└────────┬────────┘
         │
         │ Routes to email_distribution
         │ WITH job_id in state
         │
         ▼
┌─────────────────┐
│ Email Dist Node │
│                 │
│ 1. Extract      │
│    job_id from  │
│    state        │
│                 │
│ 2. Call:        │
│    send_student │
│    _feedback_   │
│    emails()     │
│                 │
│ 3. Report       │
│    results      │
└─────────────────┘
```

---

## Critical Implementation Details

### 1. Grading Node Must Call Tool BEFORE Exiting

**Why:** The grading node's agentic loop ends when it asks a question (no more tool calls). When the teacher responds "email students", the flow goes to the **router**, not back to the grading node. The router needs `job_id` to route correctly.

**Solution:** Call `complete_grading_workflow()` in the **SAME TURN** as asking the question.

#### Example (Essay Grading Node)

```python
# Step 7 in system prompt:
"""
**Step 7: Ask Teacher AND Call Tool (SAME TURN!)**
- **CRITICAL - You MUST do BOTH of these in the SAME turn:**
  1. First, ask: "Would you like me to email these feedback reports to your students?"
  2. Then **IMMEDIATELY call the tool**: complete_grading_workflow(job_id="<job_id>", route_to_email=False)
     - Use the EXACT job_id from download_reports_locally response
     - Use route_to_email=False (teacher hasn't responded yet)
     - This saves the job_id so the router can access it when teacher responds
"""
```

#### The Tool

```python
@tool_decorator
def complete_grading_workflow(job_id: str, route_to_email: bool) -> str:
    """Complete the grading workflow and set routing for next step.

    Args:
        job_id: The job ID from the grading process
        route_to_email: Whether to route to email distribution (True) or end (False)

    Returns:
        Confirmation message
    """
    routing_state["job_id"] = job_id
    routing_state["next_step"] = "email_distribution" if route_to_email else "END"
    if route_to_email:
        return f"✓ Routing configured: Proceeding to email distribution with job_id={job_id}"
    else:
        return f"✓ Workflow complete for job_id={job_id}"
```

#### Node Return

```python
# At end of grading node:
return {
    "next_step": routing_state["next_step"],  # "END" until teacher confirms
    "job_id": routing_state["job_id"],        # CRITICAL: Pass job_id to state
    "messages": messages[len(state["messages"]):],
}
```

---

### 2. Router Node Detects Email Intent

The router checks if there's a pending `job_id` and the user is asking about email.

#### Router Logic (nodes.py:58-92)

```python
async def router_node(state: AgentState) -> AgentState:
    # Extract current state
    job_id = state.get("job_id")
    last_message = state["messages"][-1].content.lower() if state["messages"] else ""

    # Keywords that indicate email intent
    email_keywords = ["email", "send", "distribute", "mail", "yes", "yeah", "yep", "sure", "ok", "okay"]

    # If there's a job_id AND user is asking about email → route to email distribution
    if job_id and any(keyword in last_message for keyword in email_keywords):
        return {
            "next_step": "email_distribution",
            "job_id": job_id,  # CRITICAL: Pass through the job_id
            "messages": [AIMessage(content=f"Great! Let me help you distribute these via email. (Using job_id: {job_id})")],
        }

    # Otherwise, use LLM to decide routing
    # ... (normal routing logic)
```

**Key Points:**
- Checks for `job_id` in state
- Detects email keywords in user message
- Overrides normal routing when both conditions are met
- **Must pass `job_id` through** to next node

---

### 3. Email Distribution Node Uses job_id

The email node extracts `job_id` from state and sends emails.

#### Email Node (nodes.py:798-944)

```python
async def email_distribution_node(state: AgentState) -> AgentState:
    # Extract job_id from state
    job_id_from_state = state.get("job_id")

    # Safety check: ensure we have a job_id
    if not job_id_from_state:
        return {
            "next_step": "END",
            "messages": [
                AIMessage(
                    content="⚠️ Error: No job_id was provided from the grading workflow. "
                    "Please complete a grading task first before attempting to send emails."
                )
            ],
        }

    # System prompt includes job_id
    system_prompt = f"""You are an automated email distribution system. A grading job (job_id: {job_id_from_state}) has completed.

    **YOUR ONLY TASK: CALL ONE TOOL AND REPORT THE RESULTS**

    **Step 1: Send emails (ONE TIME ONLY)**
    - Call send_student_feedback_emails(job_id="{job_id_from_state}") EXACTLY ONCE
    - The tool returns a summary of sent/skipped students

    **Step 2: Report results and STOP**
    - After the tool returns, report the results and STOP
    """

    # ... (rest of email logic)
```

**Key Points:**
- Extracts `job_id` from `state.get("job_id")`
- Validates job_id exists (returns error if missing)
- Injects job_id into system prompt for agent
- Calls `send_student_feedback_emails(job_id)`

---

## State Schema

The `AgentState` must include these fields:

```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_step: str
    job_id: Optional[str]  # CRITICAL: Carries job_id between nodes
```

---

## Integration Checklist for New Workflows

When adding email distribution to a new grading workflow:

### ✅ 1. Add Routing Control Tool

Add this tool to your grading node's tools list:

```python
routing_state = {"next_step": "END", "job_id": None}

@tool_decorator
def complete_grading_workflow(job_id: str, route_to_email: bool) -> str:
    routing_state["job_id"] = job_id
    routing_state["next_step"] = "email_distribution" if route_to_email else "END"
    if route_to_email:
        return f"✓ Routing configured: Proceeding to email distribution with job_id={job_id}"
    else:
        return f"✓ Workflow complete for job_id={job_id}"
```

### ✅ 2. Update System Prompt

Add instructions to ask about emailing AND call the tool:

```
**Step X: Ask Teacher AND Call Tool (SAME TURN!)**
- Ask: "Would you like me to email these feedback reports to your students?"
- **IMMEDIATELY call**: complete_grading_workflow(job_id="<job_id>", route_to_email=False)
- This saves job_id in state for router to access
```

### ✅ 3. Return job_id in State

At the end of your grading node:

```python
return {
    "next_step": routing_state["next_step"],
    "job_id": routing_state["job_id"],  # Don't forget this!
    "messages": messages[len(state["messages"]):],
}
```

### ✅ 4. Ensure Router Has Email Keywords

Make sure your router has the keyword detection logic (already present in `router_node`).

### ✅ 5. Connect to Email Distribution Node

In your graph definition, ensure the router can route to `email_distribution`:

```python
workflow.add_conditional_edges(
    "router",
    route_decision,
    {
        "essay_grading": "essay_grading",
        "test_grading": "test_grading",
        "email_distribution": "email_distribution",  # Must be present
        "general": "general",
        "END": END,
    }
)
```

---

## Testing Your Integration

### Test 1: Verify job_id Passes Through

Add debug logging to verify job_id propagates:

```python
print(f"[ROUTER DEBUG] job_id in state: {state.get('job_id')}")
print(f"[EMAIL NODE DEBUG] Received job_id: {state.get('job_id')}")
```

### Test 2: Run Full Workflow

1. Complete a grading task
2. When asked "Would you like to email...", respond "yes"
3. Verify router routes to email_distribution
4. Verify email node receives job_id
5. Verify emails are sent (or skipped with reasons)

### Test 3: Check for Common Issues

**Issue:** `job_id=None` in email node
- **Cause:** Grading node didn't call `complete_grading_workflow()`
- **Fix:** Ensure tool is called in SAME turn as asking question

**Issue:** Router doesn't route to email
- **Cause:** No email keywords detected
- **Fix:** Check keyword list, or user response doesn't match

**Issue:** "No job_id was provided" error
- **Cause:** State not passing job_id between nodes
- **Fix:** Check node return statements include `"job_id": job_id`

---

## Example: Adding Email to New Workflow

Let's say you're creating a new `lab_report_grading_node`:

```python
async def lab_report_grading_node(state: AgentState) -> AgentState:
    """Lab report grading node with email integration."""

    system_prompt = """You are a lab report grading assistant.

    [... grading instructions ...]

    **Final Step: Ask Teacher AND Call Tool (SAME TURN!)**
    - After generating reports, ask: "Would you like me to email these lab reports to your students?"
    - **IMMEDIATELY call**: complete_grading_workflow(job_id="<job_id_from_reports>", route_to_email=False)
    """

    # Setup routing control
    routing_state = {"next_step": "END", "job_id": None}

    @tool_decorator
    def complete_grading_workflow(job_id: str, route_to_email: bool) -> str:
        routing_state["job_id"] = job_id
        routing_state["next_step"] = "email_distribution" if route_to_email else "END"
        return f"✓ Workflow complete for job_id={job_id}"

    # Get tools
    tools = await get_grading_tools()
    tools = tools + [complete_grading_workflow]  # Add routing tool

    # Bind tools to LLM
    llm = get_llm().bind_tools(tools)

    # Agentic loop
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        # Execute tool calls...
        # (standard tool execution loop)

        iteration += 1

    # Return state with job_id
    return {
        "next_step": routing_state["next_step"],
        "job_id": routing_state["job_id"],  # CRITICAL!
        "messages": messages[len(state["messages"]):],
    }
```

---

## Troubleshooting

### Debug Outputs

Add these to track state flow:

```python
# In router_node
print(f"[ROUTER] job_id={state.get('job_id')}, last_message={last_message}")

# In email_distribution_node
print(f"[EMAIL] Received job_id={state.get('job_id')}")
print(f"[EMAIL] Full state keys: {state.keys()}")
```

### Common Patterns

**Pattern 1: Teacher says "no" to emailing**
- Grading node called `complete_grading_workflow(route_to_email=False)`
- State has `next_step: "END"` and `job_id: "job_..."`
- Router sees "no" keyword → routes to END
- Workflow ends gracefully

**Pattern 2: Teacher says "yes" to emailing**
- Grading node called `complete_grading_workflow(route_to_email=False)`
- State has `next_step: "END"` and `job_id: "job_..."`
- Router sees "yes" keyword + job_id → overrides to `email_distribution`
- Email node receives job_id and sends emails

**Pattern 3: Re-running email for same job**
- Email system is **idempotent** (tracks already-sent in `email_log.jsonl`)
- Can safely re-run with same job_id
- Already-sent students are skipped automatically

---

## Summary

**The Three Critical Rules:**

1. **Grading node**: Call `complete_grading_workflow()` in SAME turn as asking about email
2. **State management**: Always return `job_id` in state from grading node
3. **Router**: Must pass `job_id` through when routing to email_distribution

Follow these patterns and your workflow will seamlessly integrate with the email distribution system!
