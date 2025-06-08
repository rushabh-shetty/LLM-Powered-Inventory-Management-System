import os
import requests
import re
import collect_data
import upgrade_recommender_2
import m_monitor_system_analyzer
import time
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
from langchain_community.vectorstores import Chroma
from langchain.prompts import ChatPromptTemplate
from langchain_xai import ChatXAI
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from sentence_transformers import SentenceTransformer
from langchain.embeddings.base import Embeddings
from typing import List

# Custom Embeddings class for SentenceTransformer
class SentenceTransformerEmbeddings(Embeddings):
    def __init__(self, model_name: str):
        self.model = SentenceTransformer(model_name)
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self.model.encode(texts).tolist()
    
    def embed_query(self, text: str) -> List[float]:
        return self.model.encode([text])[0].tolist()

# Environment variables and API key loading remain unchanged
try:
    with open("api_key.txt", "r", encoding="utf-8-sig") as f:
        XAI_API_KEY = f.read().strip()
    if not XAI_API_KEY:
        print("Error: api_key.txt is empty. Please add your xAI API key to api_key.txt.")
        exit(1)
    print("Using API key:", XAI_API_KEY[:8] + "***" + XAI_API_KEY[-4:])
    # Set the environment variable
    os.environ['XAI_API_KEY'] = XAI_API_KEY
except FileNotFoundError:
    print("Error: api_key.txt not found. Create a file named api_key.txt and add your xAI API key.")
    exit(1)

# Other functions (ask_grok_cat, extract_category, etc.) remain unchanged

def ask_grok_cat(prompt):
    """Use Grok to categorize the user query."""
    switch = """ Look at the above query and assign it to one of the category numbers below:
    1 - Asking about system information such as hardware details and capacity
    2 - Asking to monitor the system or asking for thresholds for a component or the entire system
    3 - Asking for a recommendation about components or upgrades similar to those installed and where to buy them"""
    
    full_prompt = prompt + "\n\n" + switch

    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-3-latest",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant for managing and analyzing Linux servers."},
            {"role": "user", "content": full_prompt}
        ],
        "stream": False,
        "temperature": 0
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        return f"Error contacting Grok API: {e}"

def extract_category(response):
    """Extract the category number from Grok's response."""
    match = re.search(r'\d+', response)
    return int(match.group()) if match else None

def ask_cat1(prompt):
    #system_info.txt

    """Handle category 1: System information queries using RAG."""
    inventory_data = ""

    # Load hardware data from text files
    try:
        print("Checking file")
        with open("system_info.txt", "r", encoding="utf-8", errors="ignore") as file:
            content = file.read()
            inventory_data = content
    except FileNotFoundError:
        return "Error: Inventory data file not found."
    except Exception as e:
        return f"Error reading inventory files: {e}"

    
    raw_data = inventory_data
    
    documents = [Document(page_content=content, metadata={"source": raw_data})]
    
    # Split documents
    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=300, chunk_overlap=50)
    splits = text_splitter.split_documents(documents)
    
    # Create vector store and retriever with custom embeddings
    embedding = SentenceTransformerEmbeddings('all-MiniLM-L6-v2')
    vectorstore = Chroma.from_documents(documents=splits, embedding=embedding)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 1})
    
    # Define prompt template
    template = """Answer the question based only on the following context:
    {context}
    
    Question: {question}
    """
    prompt_template = ChatPromptTemplate.from_template(template)
    
    # Initialize LLM
    llm = ChatXAI(model_name="grok-3-latest", temperature=0)
    
    # Set up RAG chain
    rag_chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt_template
        | llm
        | StrOutputParser()
    )
    
    # Answer the query
    return rag_chain.invoke(prompt)

# Other category functions (ask_cat2, ask_cat3, ask_cat4) remain unchanged

def ask_cat2(prompt):
    """Handle category 2: System monitoring (placeholder)."""
    #Execute the monitor program
    #Set intervals 15 secs
    #Thresholds with grok
    #Collect all interval data 
    #Output data 
    #Provide Insight
    

    raw_data = m_monitor_system_analyzer.main()
    
    documents = [Document(page_content=raw_data, metadata={"source": raw_data})]
    
    # Split documents
    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=300, chunk_overlap=50)
    splits = text_splitter.split_documents(documents)
    
    # Create vector store and retriever with custom embeddings
    embedding = SentenceTransformerEmbeddings('all-MiniLM-L6-v2')
    vectorstore = Chroma.from_documents(documents=splits, embedding=embedding)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 1})
    
    # Define prompt template
    template = """Answer the question based only on the following context:
    {context}
    
    Question: {question}
    """
    prompt_template = ChatPromptTemplate.from_template(template)
    
    # Initialize LLM
    llm = ChatXAI(model_name="grok-3-latest", temperature=0)
    
    # Set up RAG chain
    rag_chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt_template
        | llm
        | StrOutputParser()
    )
    
    # Answer the query
    return rag_chain.invoke(prompt)


def ask_cat3(prompt):
    """Handle category 4: Component similarity (placeholder)."""
    #Run uprade_recommender
    #Run 1
    #Run a general budget of 10,000
    collect_data.main()
    
    return upgrade_recommender_2.main(chat_bot=True)

def ask_category(prompt):
    """Main function to route query to appropriate RAG system."""
    cat_response = ask_grok_cat(prompt)
    cat = extract_category(cat_response)
    
    if cat == 1:
        collect_data.main()
        timeout = 10  # Maximum wait time in seconds
        start_time = time.time()
        while not os.path.exists("system_info.txt"):
            if time.time() - start_time > timeout:
                return f"Error: Timed out waiting for file to be created."

        return ask_cat1(prompt) + "<br> <b>Complete system information can be found at system_info.txt of this repository</b>"
    elif cat == 2:
        return ask_cat2(prompt)
    elif cat == 3:
        print("Executed upgrade func")
        return ask_cat3(prompt)
    else:
        return "Sorry, I can't answer your question. Category not recognized or implemented."
    
def main(prompt):
    return ask_category(prompt)

#if __name__ == "__main__":
    #query = "Can you fine tune or improve the performance of the systems?"
    #answer = ask_category(query)
    #print(answer)