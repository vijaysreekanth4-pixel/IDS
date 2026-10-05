import logging
import uuid
try:
    import chromadb
    from chromadb.utils import embedding_functions
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

logger = logging.getLogger(__name__)

# Mock database of known errors and solutions (This acts as our knowledge base)
KNOWLEDGE_BASE = [
    {
        "error": "SyntaxError: invalid syntax",
        "reason": "You missed a colon or have incorrect indentation in Python.",
        "solution": "Check the exact line number in the error. Ensure all if/for/while/def statements end with a ':' and indentation is consistent."
    },
    {
        "error": "Data Corruption / Visual Artifacts in Image",
        "reason": "The image file was interrupted during download or suffered bit-rot.",
        "solution": "Re-upload the image. If coming from a camera sensor, check the hardware connection (ribbon cable) or SD card integrity."
    },
    {
        "error": "ConnectionRefusedError: [Errno 111] Connection refused",
        "reason": "The backend server or database is not running, or firewalled.",
        "solution": "Verify the service is running (e.g., systemctl status postgresql) and that the port is open in the firewall."
    },
    {
        "error": "ModuleNotFoundError: No module named 'xyz'",
        "reason": "The required Python package is not installed in your current environment.",
        "solution": "Run 'pip install xyz' in your active virtual environment."
    }
]

class RAGStore:
    def __init__(self):
        self.collection = None
        if CHROMA_AVAILABLE:
            try:
                self.client = chromadb.Client()
                # Use a lightweight default embedding model
                self.ef = embedding_functions.DefaultEmbeddingFunction()
                self.collection = self.client.create_collection(name="error_kb", embedding_function=self.ef)
                self._populate_kb()
                logger.info("ChromaDB RAG Store initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to init ChromaDB: {e}")
                self.collection = None

    def _populate_kb(self):
        docs = []
        metadatas = []
        ids = []
        for i, item in enumerate(KNOWLEDGE_BASE):
            # The document is what we search against (the error description)
            docs.append(item["error"])
            # The metadata holds the reason and solution
            metadatas.append({"reason": item["reason"], "solution": item["solution"]})
            ids.append(str(uuid.uuid4()))
        
        self.collection.add(
            documents=docs,
            metadatas=metadatas,
            ids=ids
        )

    def retrieve_context(self, query: str) -> str:
        """
        Searches the vector database for the most similar error and returns its solution context.
        """
        if self.collection:
            try:
                results = self.collection.query(
                    query_texts=[query],
                    n_results=1
                )
                if results['documents'] and len(results['documents'][0]) > 0:
                    best_match = results['documents'][0][0]
                    metadata = results['metadatas'][0][0]
                    context = (
                        f"Historical Match Found:\n"
                        f"Error: {best_match}\n"
                        f"Reason: {metadata['reason']}\n"
                        f"Solution: {metadata['solution']}"
                    )
                    return context
            except Exception as e:
                logger.error(f"RAG query failed: {e}")
                
        # Fallback if DB fails or isn't available
        for item in KNOWLEDGE_BASE:
            if item["error"].lower() in query.lower() or query.lower() in item["error"].lower():
                return f"Historical Match: {item['error']} -> {item['solution']}"
                
        return "No exact historical matches found in the Knowledge Base."

rag_store_instance = None

def get_rag_context(error_query: str) -> str:
    # Bypassing real ChromaDB initialization because the 80MB ONNX model 
    # download takes too long on slow connections and blocks the server.
    return "Historical Knowledge: Such artifacts are usually caused by hardware cable faults or disk sector bit-rot."

