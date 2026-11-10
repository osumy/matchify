import time
from kaggle.api.kaggle_api_extended import KaggleApi
from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionLogsStreamRequest

def check_latest(kernel_slug: str):
    api = KaggleApi()
    api.authenticate()
    client = api.build_kaggle_client()

    req = ApiGetKernelSessionLogsStreamRequest()
    req.user_name = "amiraliaraghi"
    req.kernel_slug = kernel_slug

    resp = client.kernels.kernels_api_client.get_kernel_session_logs_stream(req)

    recent = []
    start = time.time()
    for chunk in resp.iter_lines():
        if chunk:
            decoded = chunk.decode("utf-8", errors="replace")
            recent.append(decoded)
            if len(recent) > 100:
                recent.pop(0)
            if time.time() - start > 10:
                break

    print(f"Total lines sampled: {len(recent)}")
    for l in recent[-10:]:
        print(l)

if __name__ == "__main__":
    check_latest("matchify-neshan-shard1")
