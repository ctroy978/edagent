# Conversation Flow Fix - Discovery Questions First

## Problem Identified
The agent was jumping straight to asking for materials without understanding the assignment context. When a teacher said "I have essays to grade," the agent immediately asked for essays and rubric, without discovering:
- Are they handwritten or typed?
- Was there a specific test question?
- Did students use reading materials?
- How many students?

This led to a generic, one-size-fits-all approach instead of tailored assistance.

## Solution Implemented

### Added Phase 0: Understand Assignment Context

The essay grading node now follows this improved flow:

#### **Phase 0: Discovery Questions (ONE AT A TIME)**

1. **Q1: "Are the essays handwritten or typed?"**
   - Sets expectations for OCR quality
   - Determines if normalization may be needed

2. **Q2: "Was there a specific question or essay prompt for this assignment?"**
   - Critical for evaluation context
   - Helps the AI understand what students were supposed to write about

3. **Q3: "Did students use specific reading materials or sources for these essays?"**
   - Determines if RAG (knowledge base) should be used
   - Examples: textbook chapters, articles, lecture notes

4. **Q4: "How many students' essays are you grading?"**
   - Sets processing time expectations
   - Helps verify student detection later

#### **Phase 1: Gather Materials (Only After Context Is Clear)**
- Essays (upload via 📎)
- Rubric (upload or paste)

#### **Phase 2: Gather Optional Materials (Based on Phase 0 Answers)**
- Test question (if mentioned)
- Reading materials (if mentioned)
- Lecture notes (if mentioned)

#### **Phase 3+: Execute Pipeline**
- OCR Processing
- Inspection Checkpoint
- Privacy Protection
- Knowledge Base (if applicable)
- Evaluation
- Report Generation

## Updated Router Message

Changed from:
```
"Great! I'll help you grade essays. Let me explain what you need to know first..."
```

To:
```
"I'd be happy to help you grade those essays! To make sure I give you the best results, let me ask you a few questions about the assignment first."
```

This signals to the teacher that the agent needs to understand the context before proceeding.

## Expected Behavior Now

**User:** "My students have some big essay tests I need to grade."

**Agent:** "I'd be happy to help you grade those essays! To make sure I give you the best results, let me ask you a few questions about the assignment first.

Are the essays handwritten or typed?"

**User:** "Handwritten"

**Agent:** "Got it. Was there a specific question or essay prompt for this assignment?"

**User:** "Yes, they had to analyze symbolism in Robert Frost's poetry"

**Agent:** "Perfect. Did students use specific reading materials or sources for these essays? Like textbook chapters, articles, or class notes?"

... and so on.

## Benefits

1. **Tailored Assistance**: Agent adapts to the specific grading scenario
2. **Better Evaluation**: More context = higher quality grading
3. **Teacher Confidence**: Teacher feels heard and understood
4. **Efficient RAG Usage**: Only queries knowledge base if materials are relevant
5. **Clear Expectations**: Teacher knows what to prepare before uploading

## Testing

Restart the agent and test with:
```
User: "I have essays to grade"
```

Verify the agent:
1. Asks about handwritten vs typed first
2. Asks about test question
3. Asks about reading materials
4. Asks about number of students
5. THEN asks for essay uploads and rubric

## Files Modified

- `/home/tcoop/Work/edagent/edagent/nodes.py`
  - Updated `essay_grading_node` system prompt with Phase 0
  - Updated router messages to be less assuming
