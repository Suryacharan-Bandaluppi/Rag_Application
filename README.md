# PaySlate AI — Grounded RAG Chatbot Application

A custom, end-to-end **Retrieval-Augmented Generation (RAG)** Chatbot built specifically for **PaySlate** documentation. 

This application allows you to ingest custom documents (PDFs, Markdown, Text files), convert them into searchable mathematical vector representations, and query an AI assistant (**Groq LLM**) that answers **strictly based on your documents** with source citations and zero hallucinations.

---

## 📚 Table of Contents
1. [Overview & Features](#overview--features)
2. [What is RAG & How It Works](#what-is-rag--how-it-works)
3. [How the LLM "Learns" From Your Documents](#how-the-llm-learns-from-your-documents)
4. [Technology Stack & Component Breakdown](#technology-stack--component-breakdown)
5. [System Requirements & Installation](#system-requirements--installation)
6. [How to Run the Application](#how-to-run-the-application)
7. [Adding & Uploading Custom Documents](#adding--uploading-custom-documents)
8. [API Endpoints Reference](#api-endpoints-reference)

---

## 🌟 Overview & Features

* **Strict Grounded Generation**: Answers are built 100% from your uploaded PaySlate documents.
* **Out-of-Context Guardrail**: If a question cannot be answered from the provided documents, the system replies:
  > *"I don't know based on the provided PaySlate documents."*
* **Source Citations**: Every answer states the exact source file name used to formulate the response.
* **Modern Web Interface**: Interactive chat UI with drag-and-drop file upload, live vector database stats, and preset sample queries.
* **Resilient Multi-Engine Vector Store**: Automatically selects **Pinecone (Cloud)** ➔ **ChromaDB (Local)** ➔ **InMemory Vector Store (Zero-Dependency)**.
* **Fast Groq LLM Integration**: Uses Groq's high-speed API (`openai/gpt-oss-120b`) via standard HTTP requests without requiring third-party SDK dependencies.

---

## 🧬 What is RAG & How It Works

### Why Standard LLMs Fail on Private Files
Standard AI models (like ChatGPT or Claude) were pre-trained on public internet data. They **do not know anything about your company's internal PaySlate documents**. When asked about your specific policies or software features, a standard LLM will either guess or make up a fake answer (**hallucination**).

### The RAG Solution
**Retrieval-Augmented Generation (RAG)** solves this problem in two phases:

```
                  ===================================================
                                PHASE 1: INGESTION
                  ===================================================

  [ PaySlate PDF / Document ]
             │
             ▼
    1. Text Extraction      (Extract plain text from PDF, TXT, MD)
             │
             ▼
    2. Text Chunking        (Split long text into 500-character overlapping chunks)
             │
             ▼
    3. Vector Embeddings    (Convert text chunks into 384-dimensional number vectors)
             │
             ▼
    4. Vector DB Storage    (Store vectors + raw text + metadata in Pinecone / ChromaDB)


                  ===================================================
                          PHASE 2: RETRIEVAL & GENERATION
                  ===================================================

  [ User Question: "What is the parental leave policy?" ]
             │
             ▼
    1. Query Vectorization  (Convert question into a 384-dimensional query vector)
             │
             ▼
    2. Similarity Search    (Find top-5 closest vector matches using Cosine Distance)
             │
             ▼
    3. Grounded Prompting   (Pass retrieved document chunks into LLM prompt as context)
             │
             ▼
    4. Groq LLM Answer      (Generate concise answer + list cited document sources)
```

---

## 🧠 How the LLM "Learns" From Your Documents

A common question is: *"Does the AI get retrained when I upload my PDF?"*

**No, the LLM is NOT fine-tuned or retrained.** 

Instead, the application uses **In-Context Learning**:

1. **Vector Search acts as a digital librarian**: When you ask a question, the vector store searches millions of text passages in milliseconds and fetches only the **top-5 relevant paragraphs**.
2. **Context Injection**: The application dynamically pastes those 5 paragraphs into the LLM prompt right before your question:
   ```text
   SYSTEM: Answer ONLY using the facts in the Context below. If not found, say "I don't know".

   CONTEXT:
   [Document: PaySlate_HR_Policy.txt]
   Primary caregivers receive 16 weeks of 100% fully paid parental leave.

   QUESTION: What is the parental leave policy?
   ```
3. **Reasoning**: The LLM reads the injected context inside its prompt memory (**Context Window**) and synthesizes a clear human response.

---

## 🛠️ Technology Stack & Component Breakdown

| File / Component | Tech Stack | Role in Application |
| :--- | :--- | :--- |
| **[`app/main.py`](file:///Users/kasiguruprasad/Desktop/RAG/app/main.py)** | FastAPI, Uvicorn | Web API server exposing REST endpoints and mounting static Web UI. |
| **[`app/config.py`](file:///Users/kasiguruprasad/Desktop/RAG/app/config.py)** | Python `os`, `dotenv` | Loads environment variables (`GROQ_API_KEY`, chunk size, model name). |
| **[`app/ingestion.py`](file:///Users/kasiguruprasad/Desktop/RAG/app/ingestion.py)** | PyPDF, LangChain / PurePython | Extracts text from PDFs/text files and splits long documents into 500-char chunks with 50-char overlap. |
| **[`app/embeddings.py`](file:///Users/kasiguruprasad/Desktop/RAG/app/embeddings.py)** | SentenceTransformers / MD5 Hash | Converts text into 384-dimensional vectors using HuggingFace `all-MiniLM-L6-v2` or deterministic MD5 feature hashing. |
| **[`app/vectorstore.py`](file:///Users/kasiguruprasad/Desktop/RAG/app/vectorstore.py)** | Pinecone / ChromaDB / InMemory | Vector database interface supporting similarity search and index management. |
| **[`app/rag_engine.py`](file:///Users/kasiguruprasad/Desktop/RAG/app/rag_engine.py)** | Groq API (`urllib`) | Orchestrates top-K retrieval, prompt assembly, Groq API execution, and source citations. |
| **[`static/`](file:///Users/kasiguruprasad/Desktop/RAG/static/)** | HTML5, Vanilla CSS, JS | Modern dark glassmorphism Web Chat UI with drag-and-drop file uploader. |

---

## 💻 System Requirements & Installation

### Requirements
* **Python 3.9+**
* **Free Groq API Key** (Get yours free at [https://console.groq.com](https://console.groq.com))

### 1. Install Dependencies
Run the installation command in your project directory:
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Open [`.env`](file:///Users/kasiguruprasad/Desktop/RAG/.env) and insert your Groq API key on Line 2:
```ini
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b

# Vector Store Engine ('chroma' for local, 'pinecone' for cloud)
VECTOR_STORE_TYPE=chroma

# Chunking Parameters
CHUNK_SIZE=500
CHUNK_OVERLAP=50
TOP_K=5
```

---

## 🚀 How to Run the Application

Start the local server with Uvicorn:
```bash
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser and visit:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 📁 Adding & Uploading Custom Documents

### Can I upload custom documents into `data/payslate_sample_docs/`?
**YES, absolutely!** You can upload documents in three easy ways:

#### Option 1: Place Files in `data/payslate_sample_docs/` (Auto-Ingestion)
1. Drop any `.pdf`, `.txt`, or `.md` files into [`data/payslate_sample_docs/`](file:///Users/kasiguruprasad/Desktop/RAG/data/payslate_sample_docs/).
2. When you start the app, it automatically detects empty vector stores and **indexes all files in that folder into the vector database**!

#### Option 2: Upload via Web UI (Drag & Drop)
1. Open **http://127.0.0.1:8000** in your browser.
2. Drag and drop any file into the left sidebar **Drop Zone** or click to select files from your computer.

#### Option 3: Upload via REST API
```bash
curl -X POST http://127.0.0.1:8000/api/upload \
  -F "file=@/path/to/your/document.pdf"
```

---

## 🔌 API Endpoints Reference

| Endpoint | Method | Description | Sample Payload |
| :--- | :---: | :--- | :--- |
| `/api/upload` | `POST` | Upload and vectorize a new document file (`file`). | Multipart form data |
| `/api/query` | `POST` | Query the RAG chatbot for a grounded answer. | `{"query": "What is the refund policy?", "top_k": 5}` |
| `/api/stats` | `GET` | Retrieve vector store engine and total chunk count. | None |
| `/api/clear` | `DELETE` | Clear all vector chunks from the database index. | None |

### Sample Query Request & Response
```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How does PaySlate process payroll cutoff times?"}'
```

**Response:**
```json
{
  "answer": "PaySlate enforces specific approval deadlines:\n- Bi-weekly payroll: Approved by Tuesday 5:00 PM EST for Friday payout.\n- Monthly payroll: Approved by 25th of month for payout on 1st of following month.",
  "sources": [
    "PaySlate_Overview.md",
    "PaySlate_API_Integration_Guide.md"
  ],
  "context_retrieved": 5,
  "error": null
}
```
