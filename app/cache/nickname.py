import re
from redis.commands.search.field import TextField, NumericField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

class SeatLockCache:
    """Cache client wrapper managing Redis Stack search autocomplete index."""
    INDEX_NAME = "idx:nicknames"
    DOC_PREFIX = "nickname:"

    def __init__(self, redis_client):
        self.redis_client = redis_client

    async def init_index(self) -> None:
        """Initializes the search index if it doesn't already exist."""
        await self.create_index()

    async def create_index(self) -> None:
        """Creates the search index mapping nickname Hash documents."""
        schema = (
            TextField("name", weight=1.0),
            NumericField("score", sortable=True),
            # Named 'user_payload' instead of 'payload' to avoid redis-py Document constructor conflicts.
            TextField("user_payload")
        )
        definition = IndexDefinition(
            prefix=[self.DOC_PREFIX],
            index_type=IndexType.HASH
        )
        try:
            await self.redis_client.ft(self.INDEX_NAME).create_index(schema, definition=definition)
        except ResponseError as e:
            if "Index already exists" not in str(e):
                raise e

    async def add_suggestion(self, term: str, score: float = 1.0, payload: str | None = None) -> None:
        """Saves a nickname Hash document to Redis, making it searchable."""
        key = f"{self.DOC_PREFIX}{term}"
        mapping = {"name": term, "score": score}
        if payload is not None:
            mapping["user_payload"] = payload
        await self.redis_client.hset(key, mapping=mapping)

    async def add_suggestions_bulk(self, names: list[str]) -> None:
        """Seeds a bulk list of nicknames into Redis using pipeline."""
        async with self.redis_client.pipeline(transaction=False) as pipe:
            for name in names:
                key = f"{self.DOC_PREFIX}{name}"
                mapping = {"name": name, "score": 1.0}
                await pipe.hset(key, mapping=mapping)
            await pipe.execute()

    async def clear_suggestions(self) -> None:
        """Deletes the search index and all associated documents, then recreates the index."""
        try:
            # delete_documents=True cleans up both index definitions and all associated hash documents.
            await self.redis_client.ft(self.INDEX_NAME).dropindex(delete_documents=True)
        except ResponseError as e:
            if "Unknown Index name" not in str(e) and "Unknown index" not in str(e):
                raise e
        await self.create_index()

    async def get_suggestions(self, prefix: str, fuzzy: bool = False, limit: int = 10) -> list[dict]:
        """Queries the search index for prefix/fuzzy matches."""
        # Replace non-alphanumeric chars (excluding hyphens) with spaces to prevent query syntax injection.
        cleaned = re.sub(r"[^\w\s\-]", " ", prefix)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return []

        words = cleaned.split()
        if fuzzy:
            query_terms = [f"%{w}%" for w in words]
        else:
            query_terms = [f"*{w}*" for w in words]

        query_str = f"@name:({' '.join(query_terms)})"
        
        # Sort by score descending to rank higher popularity suggestions first.
        q = Query(query_str).sort_by("score", asc=False).paging(0, limit)
        results = await self.redis_client.ft(self.INDEX_NAME).search(q)

        suggestions = []
        if results and results.docs:
            for doc in results.docs:
                suggestions.append({
                    "term": getattr(doc, "name", ""),
                    "score": float(getattr(doc, "score", 1.0)) if hasattr(doc, "score") else 1.0,
                    "payload": getattr(doc, "user_payload", None)
                })
        return suggestions
