import os
import asyncio
from dotenv import load_dotenv

from fastapi import FastAPI
from langchain_core.output_parsers import PydanticOutputParser
from langgraph.graph import StateGraph,END,START
from typing import TypedDict,Optional,Literal
from pydantic import BaseModel,Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()


GROQ_API_KEY=os.getenv("GROQ_API_KEY")

app=FastAPI(title="ToneForgeAI",description="An AI agent that polishes our ordinary lame emails into various tones and styles.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


analyser_llm=ChatGroq(model="openai/gpt-oss-120b",temperature=0,groq_api_key=GROQ_API_KEY)
business_llm=ChatGroq(model="openai/gpt-oss-120b",temperature=0.4,groq_api_key=GROQ_API_KEY)
academic_llm=ChatGroq(model="openai/gpt-oss-120b",temperature=0.4,groq_api_key=GROQ_API_KEY)
corporate_llm=ChatGroq(model="openai/gpt-oss-120b",temperature=0.4,groq_api_key=GROQ_API_KEY)


class AnalysisOutput(BaseModel):
    already_formal: bool = Field(description="True if email is already formal")
    detected_category: Literal["business", "academic", "corporate", "unknown"]
    main_points: str = Field(description="Extracted or original main content")


class StructuredEmail(BaseModel):
   subject: str 
   sender: str
   to: str
   cc: Optional[str]
   body: str

class EmailState(TypedDict):
  raw_email: str
  category: Literal["business","academic","corporate"]
  analysis: Optional[AnalysisOutput]
  final_email: Optional[StructuredEmail]


analysis_parser=PydanticOutputParser(pydantic_object=AnalysisOutput)
email_parser=PydanticOutputParser(pydantic_object=StructuredEmail)

BUSINESS_TEMPLATE = """
Business Email Structure Guidelines:

- Subject: Clear and concise, action-oriented.
- Sender: Professional name.
- To: Client/Partner/Professional contact.
- Body must:
    • Begin with: Dear [Receiver Name],
    • Clearly state purpose in first paragraph.
    • Include supporting explanation.
    • End with a call to action.
    • Close with: Sincerely, [Sender Name]

Tone: Professional, concise, polite, results-driven.
"""

ACADEMIC_TEMPLATE = """
Academic Email Structure Guidelines:

- Subject: Specific and academic-focused.
- Sender: Student/Researcher full name.
- To: Professor/Dr. [Last Name].
- Body must:
    • Begin with: Dear Professor/Dr. [Last Name],
    • Include polite opening line.
    • Mention course/research context.
    • Clearly state request.
    • Close with: Best regards, [Full Name + Institution]

Tone: Respectful, formal, academic.
"""

CORPORATE_TEMPLATE = """
Corporate Email Structure Guidelines:

- Subject: Project/update oriented.
- Sender: Employee name.
- To: Manager/Team/Stakeholder.
- Body must:
    • Begin with: Hello [Recipient/Team],
    • Clearly explain update or issue.
    • Provide structured information.
    • Mention next steps or deadlines.
    • Close with: Kind regards, [Name + Designation + Company]

Tone: Professional, structured, direct.
"""

analysis_format_instructions = analysis_parser.get_format_instructions()
analysis_format_instructions = analysis_format_instructions.replace("{", "{{").replace("}", "}}")

analysis_prompt = ChatPromptTemplate.from_messages([
    ("system",
     """
You are an intelligent email analyzer.

Tasks:
1. Determine if the email is already formal.
2. Detect its category:
   - business
   - academic
   - corporate
   - unknown
3. If already formal, keep content unchanged.
4. If not formal, extract structured main points.

Return ONLY JSON.

{format_instructions}
"""),
    ("human", "{raw_email}")
]).partial(
    format_instructions=analysis_format_instructions
)



def build_email_prompt(style: str, template: str):
    email_format_instructions = email_parser.get_format_instructions()
    email_format_instructions = email_format_instructions.replace("{", "{{").replace("}", "}}")

    return ChatPromptTemplate.from_messages([
        ("system",
         f"""
You are an expert {style} email writer.

STRICTLY follow these structure rules:

{template}

Use the provided main points to generate the email.

Return ONLY JSON.

{{format_instructions}}
"""
         ),
        ("human", "{main_points}")
    ]).partial(
        format_instructions=email_format_instructions
    )



async def analyze_email(state:EmailState)->EmailState:
    chain = analysis_prompt | analyser_llm | analysis_parser
    result = await chain.ainvoke({"raw_email":state["raw_email"]})
    state["analysis"]=result
    return state

def decide_next_step(state: EmailState) -> str:
    analysis = state["analysis"]
    selected = state["category"]

    if (
        analysis.already_formal
        and analysis.detected_category == selected
    ):
        return "return_direct"


    return selected

def return_original_email(state: EmailState) -> EmailState:
    state["final_email"] = StructuredEmail(
        subject="(Original Subject Preserved)",
        sender="(Original Sender)",
        to="(Original Receiver)",
        body=state["analysis"].main_points
    )
    return state

async def generate_business_email(state: EmailState) -> EmailState:
    prompt = build_email_prompt("Business", BUSINESS_TEMPLATE)
    chain = prompt | business_llm | email_parser
    result = await chain.ainvoke({"main_points": state["analysis"].main_points})
    
    state["final_email"] = result
    return state

async def generate_academic_email(state: EmailState) -> EmailState:
    prompt = build_email_prompt("Academic", ACADEMIC_TEMPLATE)
    chain = prompt | academic_llm | email_parser
    result = await chain.ainvoke({"main_points": state["analysis"].main_points})
    state["final_email"] = result
    return state

async def generate_corporate_email(state: EmailState) -> EmailState:
    prompt = build_email_prompt("Corporate", CORPORATE_TEMPLATE)
    chain = prompt | corporate_llm | email_parser
    result = await chain.ainvoke({"main_points": state["analysis"].main_points})
    state["final_email"] = result
    return state

workflow = StateGraph(EmailState)

workflow.add_node("analyze", analyze_email)
workflow.add_node("return_direct", return_original_email)
workflow.add_node("business", generate_business_email)
workflow.add_node("academic", generate_academic_email)
workflow.add_node("corporate", generate_corporate_email)

workflow.add_edge(START, "analyze")

workflow.add_conditional_edges(
    "analyze",
    decide_next_step,
    {
        "return_direct": "return_direct",
        "business": "business",
        "academic": "academic",
        "corporate": "corporate"
    }
)

workflow.add_edge("return_direct", END)
workflow.add_edge("business", END)
workflow.add_edge("academic", END)
workflow.add_edge("corporate", END)

graph=workflow.compile()

class EmailRequest(BaseModel):
  raw_email:str
  category:Literal["business","academic","corporate"]

@app.post("/formalize_email")
async def formalize_email(request:EmailRequest):
    initial_state={
        "raw_email":request.raw_email,
        "category":request.category,
        "analysis":None,
        "final_email":None
    }
    result = await graph.ainvoke(initial_state)

    return {
        "category":request.category,
        "email":result["final_email"].dict()
    }




