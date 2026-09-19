#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#define CUDA_CHECK(x) do { cudaError_t e=(x); if(e!=cudaSuccess) { std::fprintf(stderr,"%s: %s\n",#x,cudaGetErrorString(e)); return 1; } } while(0)
__global__ void fill(int* out, int count) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if(i<count) out[i]=i*3+7;
}
int main() {
    int runtime=0,driver=0;
    CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
    CUDA_CHECK(cudaDriverGetVersion(&driver));
    constexpr int count=4096;
    int* device=nullptr;
    int result[count];
    CUDA_CHECK(cudaMalloc(&device,sizeof(result)));
    fill<<<count/256,256>>>(device,count);
    CUDA_CHECK(cudaGetLastError());
    CUDA_CHECK(cudaMemcpy(result,device,sizeof(result),cudaMemcpyDeviceToHost));
    for(int i=0;i<count;++i) if(result[i]!=i*3+7) return 2;
    CUDA_CHECK(cudaFree(device));
    std::printf("PASS runtime=%d driver=%d values=%d\n",runtime,driver,count);
    return 0;
}
