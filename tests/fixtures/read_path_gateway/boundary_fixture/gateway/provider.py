"""The provider call the read path must not be able to make."""


def complete(prompt: str) -> str:
    return f"a response to {prompt}"
