import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

XAI_API_KEY = os.getenv('XAI_API_KEY')  # Set in compose env
ROUTER_URL = 'http://router:8001/v1/chat/completions'
PRIMARY_URL = 'http://primary:8000/v1/v1/chat/completions'
XAI_URL = 'https://api.x.ai/v1/chat/completions'

@app.route('/v1/completions', methods=['POST'])
def route_completion():
    data = request.json
    prompt = data['prompt']
    # Use router to decide
    router_prompt = f"""You are a strict router. ALWAYS prefer 'local' unless the query clearly requires premium/real-time/advanced capabilities.

Examples:
'hello' -> local
'what is 2+2' -> local
'tell a joke' -> local
'current news' -> xai
'write complex code' -> xai
'latest stock prices' -> xai

Query: {prompt}

Respond exactly 'local' or 'xai' (no explanation)."""

    router_data = {
        "model": "unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4",
        "messages": [{"role": "user", "content": router_prompt}],
    }

    router_resp = requests.post(ROUTER_URL, json=router_data).json()
    
    print("ROUTER PROMPT:", router_prompt, flush=True)
    print("ROUTER RESPONSE:", router_resp, flush=True)
    
    content = router_resp['choices'][0]['message'].get('content', '')
    
    decision = content.strip().lower()
    
    print("PARSED DECISION:", decision, flush=True)

    if decision == 'local':
        data_local = data.copy()
        data_local['model'] = 'unsloth/DeepSeek-R1-Distill-Llama-8B-unsloth-bnb-4bit'
        return requests.post(PRIMARY_URL, json=data).json()
    else:
        xai_data = {"model": "grok-4-1-fast-non-reasoning", "messages": [{"role": "user", "content": prompt}], "max_tokens": data.get('max_tokens', 128), "temperature": data.get('temperature', 0.7)}
        headers = {"Authorization": f"Bearer {XAI_API_KEY}"}
        return requests.post(XAI_URL, headers=headers, json=xai_data).json()        

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8002)