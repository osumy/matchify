import time
from kaggle.api.kaggle_api_extended import KaggleApi
from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionLogsStreamRequest

def inspect_logs(kernel_slug: str, max_lines: int = 50):
    api = KaggleApi()
    api.authenticate()
    client = api.build_kaggle_client()

    req = ApiGetKernelSessionLogsStreamRequest()
    req.user_name = "amiraliaraghi"
    req.kernel_slug = kernel_slug

    print(f"Connecting to live log stream for {kernel_slug}...")
    resp = client.kernels.kernels_api_client.get_kernel_session_logs_stream(req)
    print(f"Status code: {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}")

    lines = []
    start = time.time()
    for chunk in resp.iter_lines():
        if chunk:
            decoded = chunk.decode("utf-8", errors="replace")
            lines.append(decoded)
            if len(lines) >= max_lines or time.time() - start > 5:
                break

    for l in lines[-30:]:
        print(l)

if __name__ == "__main__":
    inspect_logs("matchify-neshan-shard1")
