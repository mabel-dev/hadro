# distutils: language=c++
# cython: language_level=3

from libcpp.map cimport map
from libcpp.string cimport string
from libc.stdint cimport int32_t
from libc.string cimport memcpy
from cpython.bytes cimport PyBytes_AsString  # Import the helper to get C char* from Python bytes

cdef class _MemTable:
    cdef map[int32_t, string] cpp_map

    def __init__(self):
        self.cpp_map = map[int32_t, string]()

    def add(self, int key, bytes value):
        # Convert Python bytes to std::string
        cdef string cpp_value = string()
        cpp_value.resize(len(value))  # Ensure cpp_value is large enough to hold the bytes
        # Use PyBytes_AsString to safely access the bytes buffer
        memcpy(&cpp_value[0], PyBytes_AsString(value), len(value))
        self.cpp_map[key] = cpp_value

    def get(self, int key):
        # Retrieve std::string as Python bytes
        cdef string cpp_value = self.cpp_map[key]
        return cpp_value.data()[:cpp_value.size()]

    def remove(self, int key):
        self.cpp_map.erase(key)
