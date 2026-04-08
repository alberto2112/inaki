from pydantic import BaseModel


class Embedding(BaseModel):
    vector: list[float]
    model: str

    def __len__(self) -> int:
        return len(self.vector)
