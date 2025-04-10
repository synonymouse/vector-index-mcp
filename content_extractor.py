import tiktoken
from typing import List

# Using cl100k_base encoding, common for OpenAI models
try:
    encoding = tiktoken.get_encoding("cl100k_base")
except Exception:
    print("Warning: cl100k_base encoding not found. Falling back to p50k_base.")
    try:
        encoding = tiktoken.get_encoding("p50k_base")
    except Exception:
        print(
            "Error: Neither cl100k_base nor p50k_base encoding found. Using basic character split."
        )
        encoding = None

DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 128


def chunk_content(
    content: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> List[str]:
    """
    Chunks the given content into overlapping segments based on token count.

    Args:
        content: The text content to chunk.
        chunk_size: The target size of each chunk in tokens.
        overlap: The number of tokens to overlap between consecutive chunks.

    Returns:
        A list of text chunks.
    """
    if not content:
        return []

    if overlap >= chunk_size:
        raise ValueError("Overlap must be smaller than chunk size.")

    if encoding:
        try:
            tokens = encoding.encode(content)
            chunks = []
            start_idx = 0
            while start_idx < len(tokens):
                end_idx = min(start_idx + chunk_size, len(tokens))
                chunk_tokens = tokens[start_idx:end_idx]
                chunk_text = encoding.decode(chunk_tokens)
                chunks.append(chunk_text)

                # Move start_idx for the next chunk, considering overlap
                next_start_idx = start_idx + chunk_size - overlap
                # Ensure we make progress, especially if overlap is large or chunk_size is small
                if next_start_idx <= start_idx:
                    next_start_idx = start_idx + 1  # Force minimal progress

                start_idx = next_start_idx

            # Handle edge case where the last chunk might be empty if overlap logic pushes start_idx beyond len(tokens)
            if (
                not chunks and content
            ):  # If no chunks were created but content exists (e.g., content shorter than overlap)
                chunks.append(content)  # Add the whole content as one chunk

            return chunks

        except Exception as e:
            print(
                f"Warning: Tokenization/decoding failed ({e}). Falling back to character split."
            )

    char_chunks = []
    start_idx = 0
    # Adjust chunk_size and overlap for characters (approximate)
    # Assuming ~4 chars per token as a rough estimate
    char_chunk_size = chunk_size * 4
    char_overlap = overlap * 4

    while start_idx < len(content):
        end_idx = min(start_idx + char_chunk_size, len(content))
        char_chunks.append(content[start_idx:end_idx])
        next_start_idx = start_idx + char_chunk_size - char_overlap
        if next_start_idx <= start_idx:
            next_start_idx = start_idx + 1  # Force minimal progress
        start_idx = next_start_idx

    if not char_chunks and content:
        char_chunks.append(content)

    return char_chunks
