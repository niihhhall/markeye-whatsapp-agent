import os
import sys
from typing import List
from pypdf import PdfReader
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Initialize Clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"), 
    os.getenv("SUPABASE_SERVICE_KEY")
)

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extracts all text from a PDF file."""
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
    """Simple sliding window chunking."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

def get_embedding(text: str):
    """Generates embedding using OpenAI."""
    # Replace newlines with spaces as per OpenAI recommendation
    text = text.replace("\n", " ")
    response = openai_client.embeddings.create(
        input=[text],
        model="text-embedding-3-small"
    )
    return response.data[0].embedding

def ingest_pdf(pdf_path: str):
    """Main ingestion pipeline."""
    if not os.path.exists(pdf_path):
        print(f"??? Error: File {pdf_path} not found.")
        return

    print(f"???? Reading PDF: {pdf_path}...")
    full_text = extract_text_from_pdf(pdf_path)
    
    print("?????? Chunking text...")
    chunks = chunk_text(full_text)
    print(f"??? Created {len(chunks)} chunks.")

    print("???? Generating embeddings and uploading to Supabase...")
    for i, chunk in enumerate(chunks):
        try:
            embedding = get_embedding(chunk)
            supabase.table("knowledge_base").insert({
                "content": chunk,
                "embedding": embedding,
                "metadata": {"source": os.path.basename(pdf_path), "chunk_index": i}
            }).execute()
            print(f"  [{i+1}/{len(chunks)}] Uploaded chunk")
        except Exception as e:
            print(f"  ??? Error uploading chunk {i}: {e}")

    print("\n??? Ingestion complete!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_pdf.py <path_to_pdf>")
    else:
        ingest_pdf(sys.argv[1])
