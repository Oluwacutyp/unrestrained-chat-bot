"""ORIGINAL — Enhanced Research Features (from 'Enhanced Research Features.pdf').

In the original project these snippets were pasted into app.py. Here they are
preserved as an importable module: import the search helpers directly, and call
register_research_routes(app) to add the /research endpoint, or use
generate_response_enhanced() as a drop-in upgrade of generate_response().

Requires: duckduckgo-search, requests, beautifulsoup4 (see requirements-pc.txt)
"""
from duckduckgo_search import DDGS
import requests
from bs4 import BeautifulSoup
from flask import request, jsonify


# Enhanced search with multiple categories
def advanced_search(query, search_type="text"):
    """
    Advanced search with different types

    Args:
        query: Search query string
        search_type: "text", "news", or "images"
    """
    try:
        with DDGS() as ddgs:
            if search_type == "news":
                # Search news specifically
                results = list(ddgs.news(query, max_results=5))
                summary = "📰 Latest News:\n\n"
                for i, result in enumerate(results, 1):
                    date = result.get('date', 'Recent')
                    summary += f"{i}. {result['title']}\n"
                    summary += f"   Source: {result.get('source', 'Unknown')}\n"
                    summary += f"   Date: {date}\n"
                    summary += f"   {result['body'][:200]}...\n\n"

            elif search_type == "images":
                # Search images (returns URLs)
                results = list(ddgs.images(query, max_results=5))
                summary = "🖼 Image Results:\n\n"
                for i, result in enumerate(results, 1):
                    summary += f"{i}. {result['title']}\n"
                    summary += f"   URL: {result['image']}\n"
                    summary += f"   Source: {result.get('url', '')}\n\n"

            else:
                # Regular text search with time limit
                results = list(ddgs.text(
                    query,
                    max_results=5,
                    timelimit='m'  # Last month
                ))
                summary = "🔍 Search Results:\n\n"
                for i, result in enumerate(results, 1):
                    summary += f"{i}. {result['title']}\n"
                    summary += f"   {result['body'][:250]}...\n"
                    summary += f"   🔗 {result['href']}\n\n"

            return summary if results else "No results found."

    except Exception as e:
        return f"Search error: {str(e)}"


# Fetch and summarize web pages
def fetch_webpage_content(url, max_chars=2000):
    """
    Fetch actual content from a webpage

    Args:
        url: Webpage URL
        max_chars: Maximum characters to extract
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()

        # Get text
        text = soup.get_text(separator='\n', strip=True)

        # Clean up extra whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = '\n'.join(lines)

        # Limit length
        if len(text) > max_chars:
            text = text[:max_chars] + "..."

        return text

    except Exception as e:
        return f"Could not fetch webpage: {str(e)}"


# Fact-checking helper
def fact_check_query(claim):
    """
    Search for fact-checks on a claim
    """
    try:
        with DDGS() as ddgs:
            # Search fact-checking sites
            query = f"{claim} site:snopes.com OR site:factcheck.org OR site:politifact.com"
            results = list(ddgs.text(query, max_results=3))

            if results:
                summary = "🔍 Fact Check Results:\n\n"
                for i, result in enumerate(results, 1):
                    summary += f"{i}. {result['title']}\n"
                    summary += f"   {result['body'][:200]}...\n"
                    summary += f"   🔗 {result['href']}\n\n"
                return summary
            else:
                return "No fact-check information found. Claim could not be verified."

    except Exception as e:
        return f"Fact-check search error: {str(e)}"


# Wikipedia quick lookup
def wiki_search(query):
    """
    Quick Wikipedia summary
    """
    try:
        with DDGS() as ddgs:
            query_wiki = f"{query} site:wikipedia.org"
            results = list(ddgs.text(query_wiki, max_results=1))

            if results:
                return f"📚 Wikipedia: {results[0]['body']}"
            return "No Wikipedia article found."

    except Exception as e:
        return f"Wikipedia search error: {str(e)}"


def register_research_routes(app):
    """Add the /research endpoint to a Flask app (original @app.route code)."""

    @app.route('/research', methods=['POST'])
    def research():
        """Advanced research endpoint"""
        data = request.json
        query = data.get('query', '')
        research_type = data.get('type', 'text')  # text, news, fact_check, wiki

        if not query:
            return jsonify({"error": "No query provided"}), 400

        try:
            if research_type == 'news':
                result = advanced_search(query, 'news')
            elif research_type == 'fact_check':
                result = fact_check_query(query)
            elif research_type == 'wiki':
                result = wiki_search(query)
            else:
                result = advanced_search(query, 'text')

            return jsonify({"results": result})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


# Modified generate_response with enhanced search
def generate_response_enhanced(user_message, conversation_id, use_search,
                               conversations, llm, system_prompt):
    """Enhanced version with better search integration.

    (Original used module-level globals; dependencies are parameters here so
    this module stays import-safe. Wire your app's conversations/llm/prompt.)
    """

    if conversation_id not in conversations:
        conversations[conversation_id] = []

    history = conversations[conversation_id]
    search_context = ""

    # Smart search detection
    if use_search:
        # Detect what kind of search needed
        lower_msg = user_message.lower()

        if any(word in lower_msg for word in ['news', 'latest', 'recent']):
            search_context = f"\n\n{advanced_search(user_message, 'news')}\n"
        elif any(word in lower_msg for word in ['fact', 'true', 'false', 'verify']):
            search_context = f"\n\n{fact_check_query(user_message)}\n"
        elif 'wikipedia' in lower_msg or 'wiki' in lower_msg:
            search_context = f"\n\n{wiki_search(user_message)}\n"
        else:
            search_context = f"\n\n{advanced_search(user_message, 'text')}\n"

    # Rest of the function same as before...
    prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n"

    for msg in history[-10:]:
        role = msg['role']
        content = msg['content']
        prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"

    if search_context:
        prompt += (f"<|im_start|>user\n{user_message}\n\nContext from web "
                   f"search:{search_context}<|im_end|>\n")
    else:
        prompt += f"<|im_start|>user\n{user_message}<|im_end|>\n"

    prompt += "<|im_start|>assistant\n"

    output = llm(
        prompt,
        max_tokens=512,
        temperature=0.8,
        top_p=0.95,
        repeat_penalty=1.1,
        stop=["<|im_end|>", "User:", "\nUser:", "\n\nUser:"]
    )

    response_text = output['choices'][0]['text'].strip()

    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": response_text})

    if len(history) > 20:
        conversations[conversation_id] = history[-20:]

    return response_text


# ---------------------------------------------------------------------------
# USAGE IN YOUR INTERFACE — add these buttons in index.html (from the PDF):
#
# <div class="research-buttons">
#     <button onclick="researchNews()">📰 News</button>
#     <button onclick="researchWiki()">📚 Wikipedia</button>
#     <button onclick="factCheck()">🔍 Fact Check</button>
# </div>
# <script>
# async function researchNews() {
#     const query = prompt("What news would you like to search for?");
#     if (!query) return;
#     const response = await fetch('/research', {
#         method: 'POST',
#         headers: {'Content-Type': 'application/json'},
#         body: JSON.stringify({query: query, type: 'news'})
#     });
#     const data = await response.json();
#     addMessage(data.results, 'assistant');
# }
# async function researchWiki() { ... type: 'wiki' ... }
# async function factCheck() { ... type: 'fact_check' ... }
# </script>
# ---------------------------------------------------------------------------
