# Understanding vLLM VRAM Requirements

A vLLM instance needs VRAM for three things: model weights, KV cache, and operational overhead. This document explains how to calculate each.

## 1. Model Weights

The weight size depends on the model's parameter count and quantization format.

| Quantization | Bytes per parameter | Example: 30B params | Example: 8B params |
|-------------|-------------------|---------------------|---------------------|
| FP32        | 4                 | 120 GB              | 32 GB               |
| BF16 / FP16 | 2                | 60 GB               | 16 GB               |
| FP8         | 1                 | 30 GB               | 8 GB                |
| NVFP4 / INT4 | 0.5             | 15 GB               | 4 GB                |
| AWQ 4-bit   | ~0.55 (overhead)  | ~16.5 GB            | ~4.4 GB             |

**MoE models** (like Nemotron Nano 30B) have a large total parameter count but only activate a subset per token. All parameters must be in VRAM regardless — only the compute cost is reduced, not the memory cost.

For the models in this project:

| Model | Total params | Quantization | Approximate weight size |
|-------|-------------|--------------|------------------------|
| Nemotron Nano 30B (primary) | 30B | NVFP4 | ~18 GB |
| Nemotron Orchestrator 8B (router) | 8B | AWQ 4-bit | ~6 GB |

## 2. KV Cache

The KV cache stores attention keys and values for every token in every layer of the model. This is what allows the model to "remember" the conversation — each token in the context window has an entry.

### Formula

```
bytes_per_token = 2 × num_layers × num_kv_heads × head_dim × dtype_bytes
```

| Term | Meaning |
|------|---------|
| 2 | One tensor for keys (K) + one for values (V) |
| num_layers | Number of transformer layers (from model config) |
| num_kv_heads | Number of key-value attention heads (may differ from query heads if using GQA/MQA) |
| head_dim | Dimension per head = hidden_size / num_attention_heads |
| dtype_bytes | 1 for fp8, 2 for fp16/bf16, 4 for fp32 |

### Per-sequence memory

Multiply bytes_per_token by the context length to get the KV cache cost for one sequence:

```
kv_per_sequence = bytes_per_token × max_model_len
```

### Total KV cache budget

Multiply by the maximum number of concurrent sequences:

```
total_kv_cache = kv_per_sequence × max_num_seqs
```

### Example: Nemotron Nano 30B

Architecture values (from the model's `config.json`):

```
num_hidden_layers:   52
num_key_value_heads: 2   (grouped-query attention — only 2 KV heads vs 32 query heads)
head_dim:            84  (hidden_size 2688 / num_attention_heads 32)
```

With `--kv-cache-dtype fp8` (1 byte per element):

```
bytes_per_token = 2 × 52 × 2 × 84 × 1 = 17,472 bytes ≈ 17 KB/token
```

| Context length | Per sequence | × 8 concurrent seqs |
|---------------|-------------|---------------------|
| 4,096 (4K)    | 68 MB       | 546 MB              |
| 16,384 (16K)  | 273 MB      | 2.1 GB              |
| 32,768 (32K)  | 546 MB      | 4.3 GB              |
| 131,072 (128K)| 2.2 GB      | 17.4 GB             |
| 262,144 (256K)| 4.4 GB      | 34.6 GB             |

### Example: Nemotron Orchestrator 8B

```
num_hidden_layers:   32
num_key_value_heads: 8
head_dim:            128  (hidden_size 4096 / num_attention_heads 32)
```

With `--kv-cache-dtype fp8_e4m3`:

```
bytes_per_token = 2 × 32 × 8 × 128 × 1 = 65,536 bytes ≈ 64 KB/token
```

This model has a much larger per-token KV cost due to 8 KV heads vs 2, and 128 head_dim vs 84. But at `--max-model-len 2048` with few concurrent sequences, the total is small:

| Context length | Per sequence | × 4 concurrent seqs |
|---------------|-------------|---------------------|
| 2,048         | 128 MB      | 512 MB              |

## 3. Operational Overhead

Beyond weights and KV cache, vLLM uses additional VRAM for:

- **CUDA context**: ~300-500 MB fixed cost per GPU, allocated by the driver
- **Activation memory**: temporary tensors during forward pass, scales with batch size and sequence length
- **Kernel workspace**: CUTLASS/FlashInfer kernels need scratch memory
- **Graph capture**: if using CUDA graphs (default in vLLM), the captured graphs consume additional memory

A practical estimate for overhead is **2-4 GB** depending on the model and batch size.

## Putting It Together

```
total_vram = model_weights + total_kv_cache + overhead
```

### This project's current configuration

| Component | Calculation | VRAM |
|-----------|------------|------|
| **Router (Orchestrator 8B)** | | |
| Weights (AWQ 4-bit) | 8B × 0.55 bytes | ~6 GB |
| KV cache (2K ctx, fp8) | 512 MB | ~0.5 GB |
| Overhead | | ~2 GB |
| **Router total** | | **~8.5 GB** |
| | | |
| **Primary (Nano 30B)** | | |
| Weights (NVFP4) | 30B × 0.5 bytes + overhead | ~18 GB |
| KV cache (16K ctx, fp8, 8 seqs) | 2.1 GB | ~2.1 GB |
| Overhead | | ~3 GB |
| **Primary total** | | **~23 GB** |
| | | |
| **Combined on GPU 0** | | **~31.5 GB** |

With 96 GB total VRAM, this uses about 33%. The `--gpu-memory-utilization` values (0.14 for router, 0.65 for primary) set the pre-allocation ceiling higher than what's strictly needed — vLLM fills the extra space with additional KV cache pages for better throughput, but the actual minimum requirement is much lower.

## Key Takeaways

- **Model weights dominate** at low concurrency. For single-user homelab use, KV cache is a small fraction of total VRAM.
- **KV cache dominates** at high concurrency or long context. Serving 256K context to 8 users simultaneously would need 35 GB of KV cache alone.
- **Quantizing KV cache to fp8** halves the cache cost vs fp16 with minimal quality loss. This is nearly always worth doing.
- **GQA (grouped-query attention)** dramatically reduces KV cache size. The Nano 30B uses only 2 KV heads (vs 32 query heads), making its per-token KV cost very low despite being a large model.
- **MoE doesn't help with VRAM** — all expert weights live in memory even though only a few are active per token. MoE saves compute, not memory.
