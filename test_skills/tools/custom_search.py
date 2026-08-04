from langchain_core.tools import tool

@tool
def custom_search(query: str) -> str:
    """A custom search tool that returns a specific mock string for testing dynamic loading."""
    print(f"  [Executing Real Search Tool] custom_search called with query: '{query}'")
    return f"Custom search result for '{query}': Real Search Succeeded!"
