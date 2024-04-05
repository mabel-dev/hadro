# boyer_moore.pyx
from libc.string cimport strlen
from libcpp.map cimport map
from libcpp.vector cimport vector

cdef int max(int a, int b):
    return a if a > b else b

cdef map[char, int] preprocess_bad_char_heuristic(char* pattern, int m):
    cdef:
        map[char, int] badChar
        int i
    # Initialize all occurrences as -1
    for i in range(256):
        badChar[<char>i] = -1
    # Fill the actual value of last occurrence
    for i in range(m):
        badChar[pattern[i]] = i
    return badChar

def boyer_moore_search(char* text, char* pattern):
    cdef:
        int n = strlen(text)
        int m = strlen(pattern)
        map[char, int] badChar = preprocess_bad_char_heuristic(pattern, m)
        int s = 0  # s is shift of the pattern with respect to text
        vector[int] matches  # To store positions of matches
        
    while s <= n - m:
        cdef int j = m - 1

        # Decrease index j of pattern while characters of pattern and text are matching
        while j >= 0 and pattern[j] == text[s + j]:
            j -= 1

        if j < 0:
            matches.push_back(s)
            # Shift the pattern so that the next character in text aligns with the last occurrence in pattern
            s += (m - badChar[text[s + m]] if s + m < n else 1)
        else:
            # Shift the pattern so that the bad character in text aligns with the last occurrence in pattern
            s += max(1, j - badChar[text[s + j]])

    # Convert matches to Python list before returning
    return [match for match in matches]

