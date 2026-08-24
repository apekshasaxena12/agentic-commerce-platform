# Day 13 Part 2: production image for Render (persistent container, not
# serverless — see DEPLOY.md). Two Render web services are built from this
# SAME image, differing only in their start command (set in Render's
# dashboard, no second Dockerfile needed):
#
#   Front Door 1 (chat + /webhooks/razorpay) — this image's default CMD:
#       uvicorn server.app:app --host 0.0.0.0 --port $PORT
#
#   Front Door 2 (MCP + merchant dashboard) — override the start command to:
#       python -m mcp_server.server --http --port $PORT
#
# Both need the same DATABASE_URL/RAZORPAY_*/GROQ_API_KEY env vars, set via
# Render's dashboard (not committed — see .dockerignore excluding .env).

FROM python:3.11-slim

WORKDIR /app

# CPU-only torch first, from PyTorch's own CPU wheel index: sentence-
# transformers pulls in torch as a dependency, and PyPI's default torch
# wheel drags in several GB of NVIDIA CUDA libraries that are dead weight
# here — this app only runs SentenceTransformer.encode() on CPU (no GPU on
# Render), and the full CUDA wheel was large enough to fill a real build
# host's disk during this Dockerfile's own testing. Installing the CPU
# wheel first satisfies requirements.txt's later `torch` dependency without
# pip pulling the CUDA one.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# psycopg[binary] and sentence-transformers both ship prebuilt Linux wheels
# for this base image, so no system build toolchain is needed beyond that.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the sentence-transformers model in at BUILD time (DEPLOY.md's day
# 4-5 note, implemented here): catalog/retrieval.py's _get_model() and
# db/embed_products.py both lazy-load SentenceTransformer("all-MiniLM-L6-v2")
# on first use, which would otherwise download from the HF Hub on whichever
# request happens to be live right after a deploy. Running the same
# construction here caches it into this layer's HF cache
# (~/.cache/huggingface), so every container started from this image
# already has it — first real request pays no download cost.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Force offline mode from here on: even with the model weights cached
# locally above, huggingface_hub still does a HEAD request per config file
# to check for upstream changes on every SentenceTransformer(...) call
# unless told not to — measured, with no network at all (this Dockerfile's
# own build-verification step), at 5 retries x ~23s x 5 files = ~140s
# before it gave up and fell back to the local cache. That's the exact
# "first-request delay" this bake step exists to eliminate. HF_HUB_OFFLINE
# skips those checks entirely and goes straight to the local cache — safe
# to set unconditionally now that the model is already baked in above.
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

COPY . .

# Render sets $PORT at runtime and routes external traffic to it; the app
# must bind to that port, not a hardcoded one.
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
