from typing import List, Optional, Dict, Any
from langchain_anthropic import ChatAnthropic
from langchain.schema import Document
from langchain.prompts import ChatPromptTemplate
from langchain.chains import RetrievalQA
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain
from src.config import settings
from src.database import get_vector_store
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on the provided context.
Use only the information from the context to answer the question.
If the context doesn't contain enough information, say so honestly.
Cite sources when possible by referencing the document metadata."""

QA_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "Context:\n{context}\n\nQuestion: {input}"),
    ]
)


class RAGRetriever:
    """RAG retriever with similarity search and LLM chain."""

    def __init__(self, collection_name: Optional[str] = None):
        self.vector_store = get_vector_store()
        self.collection = self.vector_store.get_collection(collection_name)
        self.llm = ChatAnthropic(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            temperature=0,
        )
        self._retrieval_chain = None

    def similarity_search(
        self,
        query: str,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        filter_dict: Optional[Dict] = None,
    ) -> List[Document]:
        """Perform similarity search on vector store."""
        top_k = top_k or settings.similarity_top_k
        threshold = threshold or settings.similarity_threshold

        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=filter_dict,
        )

        documents = []
        if results["documents"] and results["documents"][0]:
            for i, doc_text in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 1.0
                similarity = 1 - distance

                if similarity >= threshold:
                    documents.append(
                        Document(
                            page_content=doc_text,
                            metadata={**metadata, "similarity": similarity},
                        )
                    )

        logger.info(f"Found {len(documents)} relevant documents for query")
        return documents

    def get_retrieval_chain(self):
        """Get or create retrieval chain."""
        if self._retrieval_chain is None:
            retriever = self.collection.as_retriever(
                search_kwargs={"k": settings.similarity_top_k}
            )
            combine_docs_chain = create_stuff_documents_chain(self.llm, QA_PROMPT)
            self._retrieval_chain = create_retrieval_chain(
                retriever, combine_docs_chain
            )
        return self._retrieval_chain

    def query(
        self, question: str, filter_dict: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Query the RAG system."""
        chain = self.get_retrieval_chain()
        result = chain.invoke({"input": question})
        return {
            "answer": result.get("answer", ""),
            "context": result.get("context", []),
        }

    def query_with_sources(
        self, question: str, filter_dict: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Query with source documents returned."""
        docs = self.similarity_search(question)
        if not docs:
            return {
                "answer": "No relevant information found in the knowledge base.",
                "sources": [],
            }

        context = "\n\n".join(
            [
                f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}"
                for d in docs
            ]
        )

        prompt = QA_PROMPT.format(context=context, input=question)
        response = self.llm.invoke(prompt)

        sources = []
        for doc in docs:
            sources.append(
                {
                    "content": (
                        doc.page_content[:200] + "..."
                        if len(doc.page_content) > 200
                        else doc.page_content
                    ),
                    "metadata": doc.metadata,
                    "similarity": doc.metadata.get("similarity", 0),
                }
            )

        return {
            "answer": response.content,
            "sources": sources,
        }


def get_retriever(collection_name: Optional[str] = None) -> RAGRetriever:
    """Dependency injection for retriever."""
    return RAGRetriever(collection_name)
