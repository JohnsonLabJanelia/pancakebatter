#!/usr/bin/env python3
"""
TensorRT EfficientNMS Plugin Verification Script
Tests the custom-built EfficientNMS plugins and engine deserialization
Updated for Orange project structure
"""

import tensorrt as trt
import ctypes
import os
import sys

def main():
    # ✅ Load your freshly built plugin library from Orange installation
    plugin_lib_path = "/opt/orange/lib/tensorrt-oss/plugins/libnvinfer_plugin.so"
    
    print("=" * 60)
    print("TensorRT EfficientNMS Plugin Verification")
    print("=" * 60)
    
    # Check if plugin library exists
    if os.path.exists(plugin_lib_path):
        print(f"✅ Found plugin library: {plugin_lib_path}")
        try:
            ctypes.CDLL(plugin_lib_path, mode=ctypes.RTLD_GLOBAL)
            print("✅ Plugin library loaded successfully")
        except Exception as e:
            print(f"❌ Failed to load plugin library: {e}")
            sys.exit(1)
    else:
        print(f"❌ Plugin library not found at: {plugin_lib_path}")
        print("Please ensure TensorRT OSS plugins are built and installed.")
        sys.exit(1)

    # Initialize TensorRT logger
    TRT_LOGGER = trt.Logger(trt.Logger.WARNING)

    # Initialize plugin library
    print("\n🔧 Initializing TensorRT plugins...")
    try:
        trt.init_libnvinfer_plugins(TRT_LOGGER, "")
        print("✅ TensorRT plugins initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize plugins: {e}")
        sys.exit(1)

    # List available plugins to verify EfficientNMS is loaded
    print("\n🔍 Checking for EfficientNMS plugins...")
    registry = trt.get_plugin_registry()
    plugin_creators = registry.plugin_creator_list
    efficient_nms_plugins = []

    for creator in plugin_creators:
        if "EfficientNMS" in creator.name:
            plugin_info = f"{creator.name} (version: {creator.plugin_version})"
            print(f"✅ Found: {plugin_info}")
            efficient_nms_plugins.append(plugin_info)

    if not efficient_nms_plugins:
        print("❌ No EfficientNMS plugins found!")
        print("Available plugins:")
        for creator in plugin_creators[:10]:  # Show first 10 plugins
            print(f"  - {creator.name}")
        if len(plugin_creators) > 10:
            print(f"  ... and {len(plugin_creators) - 10} more plugins")
        sys.exit(1)
    else:
        print(f"✅ Found {len(efficient_nms_plugins)} EfficientNMS plugin(s)!")

    # Test engine deserialization
    engine_path = "/home/jeremy/fish4.engine"
    print(f"\n🧪 Testing engine deserialization...")
    print(f"Engine path: {engine_path}")

    if not os.path.exists(engine_path):
        print(f"❌ Engine file not found at: {engine_path}")
        print("Skipping engine deserialization test.")
        return

    try:
        with open(engine_path, "rb") as f:
            engine_data = f.read()
            print(f"📄 Engine file size: {len(engine_data):,} bytes")
            
            with trt.Runtime(TRT_LOGGER) as runtime:
                print("🔄 Deserializing engine...")
                engine = runtime.deserialize_cuda_engine(engine_data)
                
                if engine is None:
                    print("❌ Engine deserialization failed!")
                    return
                
                print("✅ Success! Engine deserialized successfully.")
                print(f"📊 Engine name: {engine.name}")
                print(f"📊 Number of I/O tensors: {engine.num_io_tensors}")
                
                # Print input/output info using new TensorRT 10.x API
                print("\n📋 Tensor Information:")
                for i in range(engine.num_io_tensors):
                    tensor_name = engine.get_tensor_name(i)
                    shape = engine.get_tensor_shape(tensor_name)
                    dtype = engine.get_tensor_dtype(tensor_name)
                    mode = engine.get_tensor_mode(tensor_name)
                    print(f"  {mode.name:<6} {i}: {tensor_name:<20} - Shape: {shape}, Type: {dtype}")

    except Exception as e:
        print(f"❌ Error during deserialization: {e}")
        import traceback
        traceback.print_exc()
        return

    print("\n" + "=" * 60)
    print("✅ All tests completed successfully!")
    print("EfficientNMS plugins are working correctly.")
    print("=" * 60)

if __name__ == "__main__":
    main()