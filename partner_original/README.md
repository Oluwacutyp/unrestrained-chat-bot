# Partner Originals — preserved source reconstructions

Faithful, runnable reconstructions of the 6 PDFs uploaded to `main`
(repaired for PDF line-wrap breaks; logic identical to the originals).

| File | Source PDF | Stack |
|---|---|---|
| `companion_backend.py` | AI Companion Backend | Flask + llama.cpp + DDG |
| `research_features.py` | Enhanced Research Features | DDG + requests + bs4 (importable add-on) |
| `local_partner.py` | FULL LOCAL Partner Bot (Alex) | Flask + CORS + llama.cpp + mood engine |
| `realistic_partner.py` | ULTRA-REALISTIC Partner Bot | Flask + llama.cpp + PIL + mood decay |
| `hf_app.py` | Hugging Face Spaces Deployment | Gradio + HF Inference API |
| `whatsapp.js` | WhatsApp Bot Handler | whatsapp-web.js → Flask `/chat` |

**These are PC-oriented** (need `llama-cpp-python` + a ~4 GB GGUF + Node).
Install: `pip install -r requirements-pc.txt`, add `models/*.gguf`, run any server.

**Termux / God Quant fusion:** the same capabilities were re-engineered
stdlib-only under `godquant/companion/` — personas, mood engine, web search,
local-GGUF provider, unified chat+quant HTTP server, and the WhatsApp bridge
works unchanged against it. See the main README's "Partner fusion" section.
