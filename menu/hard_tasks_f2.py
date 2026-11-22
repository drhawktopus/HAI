"""Hard Bench — SECOND task family: existing-code work (debug / modify), companion to hard_tasks.py.

Where hard_tasks.py is mostly "write it from scratch" (kind "impl"), this family is the other half of
the Hard Bench charter: debugging an existing function with one subtle bug, and modifying a working
function to add/change behavior WITHOUT breaking what it already does. Same grading contract as
hard_tasks.py: TASKS holds {"kind","prompt","checks"}; checks are "lambda: <expr>" strings graded in a
bounded child process by the payload; REFERENCE holds a known-good solution per task.

Two things this family adds on top of hard_tasks.py's contract:
  - ORIGINAL: the exact code shown in each task's prompt (the buggy or unmodified function), so
    selftest() can prove the checks actually DETECT the bug / the missing behavior, not just that the
    reference is correct.
  - REGRESSION: for every "modify" task, the indices into that task's checks list which describe the
    function's OLD behavior — the ORIGINAL must still pass those (they're the "don't break existing
    calls" contract), while at least one other check must fail on the ORIGINAL (that's the new
    behavior it doesn't have yet).

selftest() is the instrument's positive AND negative control: every check must be exactly True on
REFERENCE, and every ORIGINAL must fail at least one check (or, for modify tasks, must pass all its
REGRESSION checks while failing something else) — otherwise the checks can't tell right from wrong,
which is a hole in the instrument, not a passing grade.
"""

import re

TASKS = {
    "dbg_running_total": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return the list of running (cumulative) sums of nums, "
            "e.g. running_total([1,2,3]) should be [1,3,6]. It has a bug that drops the first element "
            "entirely. Return the corrected function.\n\n"
            "def running_total(nums):\n    out = []\n    total = 0\n    for i in range(1, len(nums)):\n        total += nums[i]\n        out.append(total)\n    return out"
        ),
        "checks": [
            "lambda: running_total([1,2,3])==[1,3,6]",
            "lambda: running_total([])==[]",
            "lambda: running_total([5])==[5]",
            "lambda: running_total([1,1,1,1])==[1,2,3,4]",
        ],
    },
    "dbg_count_evens": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return the count of EVEN numbers in nums, but the "
            "comparison is reversed so it actually counts odd numbers. Return the corrected function "
            "count_evens(nums).\n\n"
            "def count_evens(nums):\n    count = 0\n    for n in nums:\n        if n % 2 != 0:\n            count += 1\n    return count"
        ),
        "checks": [
            "lambda: count_evens([1,2,3,4,5,6,7])==3",
            "lambda: count_evens([])==0",
            "lambda: count_evens([1,3,5])==0",
            "lambda: count_evens([2,4,6])==3",
            "lambda: count_evens([0])==1",
        ],
    },
    "dbg_max_subarray_sum": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return the maximum sum of any contiguous subarray of "
            "nums (Kadane's algorithm), and must work even when every number is negative (in that case "
            "it should return the largest single element, never 0). The version below resets the "
            "running sum to 0 whenever it goes negative, which breaks the all-negative case. Return the "
            "corrected function max_subarray_sum(nums). An empty list should return 0.\n\n"
            "def max_subarray_sum(nums):\n    best = 0\n    cur = 0\n    for n in nums:\n        cur = cur + n\n        if cur < 0:\n            cur = 0\n        best = max(best, cur)\n    return best"
        ),
        "checks": [
            "lambda: max_subarray_sum([-2,1,-3,4,-1,2,1,-5,4])==6",
            "lambda: max_subarray_sum([1])==1",
            "lambda: max_subarray_sum([-1,-2,-3])==-1",
            "lambda: max_subarray_sum([5,4,-1,7,8])==23",
            "lambda: max_subarray_sum([])==0",
        ],
    },
    "dbg_is_palindrome": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return True if s reads the same forwards and backwards, "
            "IGNORING case and any non-alphanumeric characters (spaces, punctuation). The version below "
            "does a raw comparison so it fails on mixed case or punctuation. Return the corrected "
            "function is_palindrome(s).\n\n"
            "def is_palindrome(s):\n    return s == s[::-1]"
        ),
        "checks": [
            "lambda: is_palindrome('Racecar') is True",
            "lambda: is_palindrome('A man a plan a canal Panama') is True",
            "lambda: is_palindrome('') is True",
            "lambda: is_palindrome('hello') is False",
            "lambda: is_palindrome('Was it a car or a cat I saw') is True",
        ],
    },
    "dbg_count_vowels": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to count vowels (a, e, i, o, u) in s regardless of case, "
            "but it only checks lowercase letters so uppercase vowels are never counted. Return the "
            "corrected function count_vowels(s).\n\n"
            "def count_vowels(s):\n    count = 0\n    for c in s:\n        if c in 'aeiou':\n            count += 1\n    return count"
        ),
        "checks": [
            "lambda: count_vowels('hello')==2",
            "lambda: count_vowels('HELLO')==2",
            "lambda: count_vowels('')==0",
            "lambda: count_vowels('xyz')==0",
            "lambda: count_vowels('AEIOUaeiou')==10",
        ],
    },
    "dbg_dedupe_sorted": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to remove consecutive duplicates from an already-SORTED "
            "list, keeping one copy of each distinct value, e.g. dedupe_sorted([1,1,2,3,3,3,4]) should "
            "be [1,2,3,4]. The loop bound below is wrong and always drops the last element (and mishandles "
            "single-element or all-duplicate lists). Return the corrected function dedupe_sorted(nums).\n\n"
            "def dedupe_sorted(nums):\n    out = []\n    for i in range(len(nums) - 1):\n        if nums[i] != nums[i + 1]:\n            out.append(nums[i])\n    return out"
        ),
        "checks": [
            "lambda: dedupe_sorted([1,1,2,3,3,3,4])==[1,2,3,4]",
            "lambda: dedupe_sorted([])==[]",
            "lambda: dedupe_sorted([1])==[1]",
            "lambda: dedupe_sorted([1,2,3])==[1,2,3]",
            "lambda: dedupe_sorted([5,5,5])==[5]",
        ],
    },
    "dbg_binary_to_int": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to convert a binary string bits (most-significant bit "
            "first, e.g. '110' is 6) into its decimal integer value. The version below treats the FIRST "
            "character as the least-significant bit instead, which gives the wrong answer for anything "
            "that isn't a palindrome of bits. Return the corrected function binary_to_int(bits).\n\n"
            "def binary_to_int(bits):\n    n = 0\n    for i, b in enumerate(bits):\n        n += int(b) * (2 ** i)\n    return n"
        ),
        "checks": [
            "lambda: binary_to_int('110')==6",
            "lambda: binary_to_int('0')==0",
            "lambda: binary_to_int('1111')==15",
            "lambda: binary_to_int('100000')==32",
            "lambda: binary_to_int('1000')==8",
        ],
    },
    "dbg_second_largest": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return the second-largest DISTINCT value in nums (a "
            "non-empty list that may contain negatives and duplicates). Initializing both trackers to 0 "
            "breaks it whenever every value is negative or below 0. Return the corrected function "
            "second_largest(nums).\n\n"
            "def second_largest(nums):\n    first = second = 0\n    for n in nums:\n        if n > first:\n            second = first\n            first = n\n        elif n > second:\n            second = n\n    return second"
        ),
        "checks": [
            "lambda: second_largest([3,1,4,1,5,9,2,6])==6",
            "lambda: second_largest([-5,-1,-3])==-3",
            "lambda: second_largest([1,2])==1",
            "lambda: second_largest([10,9,9,8])==9",
            "lambda: second_largest([2,2,3])==2",
        ],
    },
    "dbg_append_to_all": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to append item to lst (a list) and return it, defaulting to "
            "a fresh empty list when lst isn't given. The mutable default argument below is shared "
            "across every call that omits lst, so unrelated calls silently accumulate into the same "
            "list. Return the corrected function append_to_all(item, lst=None), where omitting lst must "
            "still start from an empty list each time.\n\n"
            "def append_to_all(item, lst=[]):\n    lst.append(item)\n    return lst"
        ),
        "checks": [
            "lambda: append_to_all(1)==[1]",
            "lambda: append_to_all(2)==[2]",
            "lambda: append_to_all('a',[])==['a']",
            "lambda: append_to_all(3,[9])==[9,3]",
            "lambda: append_to_all(5)==[5]",
        ],
    },
    "dbg_chunk_list": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to split lst into exactly n chunks of as-equal-as-possible "
            "size (when len(lst) isn't divisible by n, the earlier chunks get one extra element each), "
            "e.g. chunk_list([1,2,3,4,5],2) should be [[1,2,3],[4,5]]. The version below uses floor "
            "division for the chunk size, which silently drops the leftover elements. Return the "
            "corrected function chunk_list(lst, n).\n\n"
            "def chunk_list(lst, n):\n    size = len(lst) // n\n    return [lst[i * size:(i + 1) * size] for i in range(n)]"
        ),
        "checks": [
            "lambda: chunk_list([1,2,3,4,5],2)==[[1,2,3],[4,5]]",
            "lambda: chunk_list([1,2,3,4,5,6],3)==[[1,2],[3,4],[5,6]]",
            "lambda: chunk_list([1,2,3],3)==[[1],[2],[3]]",
            "lambda: chunk_list([],2)==[[],[]]",
            "lambda: chunk_list([1,2,3,4,5,6,7],2)==[[1,2,3,4],[5,6,7]]",
        ],
    },
    "dbg_word_freq": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return a dict mapping each lowercased word in text to "
            "the number of times it occurs, e.g. word_freq('the cat the') should be {'the':2,'cat':1}. "
            "The version below always sets the count to 1 instead of incrementing it, so repeated words "
            "never show a count above 1. Return the corrected function word_freq(text).\n\n"
            "def word_freq(text):\n    freq = {}\n    for w in text.lower().split():\n        freq[w] = 1\n    return freq"
        ),
        "checks": [
            "lambda: word_freq('the cat sat on the mat the cat ran')=={'the':3,'cat':2,'sat':1,'on':1,'mat':1,'ran':1}",
            "lambda: word_freq('')=={}",
            "lambda: word_freq('a a a')=={'a':3}",
            "lambda: word_freq('Hello hello HELLO')=={'hello':3}",
            "lambda: word_freq('one')=={'one':1}",
        ],
    },
    "dbg_transpose": {
        "kind": "debug",
        "prompt": (
            "The function below is supposed to return the transpose of matrix m (a list of rows, not "
            "necessarily square) as a new matrix. It uses len(m) (the row count) as the bound for BOTH "
            "dimensions, which silently produces the wrong shape (or drops columns) whenever the matrix "
            "isn't square. Return the corrected function transpose(m); transpose([]) should be [].\n\n"
            "def transpose(m):\n    n = len(m)\n    return [[m[j][i] for j in range(n)] for i in range(n)]"
        ),
        "checks": [
            "lambda: transpose([[1,2,3],[4,5,6]])==[[1,4],[2,5],[3,6]]",
            "lambda: transpose([[1,2],[3,4]])==[[1,3],[2,4]]",
            "lambda: transpose([[1]])==[[1]]",
            "lambda: transpose([])==[]",
            "lambda: transpose([[1,2,3]])==[[1],[2],[3]]",
        ],
    },
    "mod_keep_positive": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter include_zero (default False) to the function below so that, "
            "when include_zero is True, 0 is also kept (in addition to positive numbers), WITHOUT "
            "breaking existing calls like keep_positive([-1,0,1,2]). Return the modified function.\n\n"
            "def keep_positive(nums):\n    return [n for n in nums if n > 0]"
        ),
        "checks": [
            "lambda: keep_positive([-1,0,1,2])==[1,2]",
            "lambda: keep_positive([])==[]",
            "lambda: keep_positive([0,0,1])==[1]",
            "lambda: keep_positive([-1,0,1,2], include_zero=True)==[0,1,2]",
            "lambda: keep_positive([-1,-2],include_zero=True)==[]",
        ],
    },
    "mod_join_words": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter sep (default ' ', a single space) to the function below so it "
            "joins words with sep instead of always using a space, WITHOUT breaking existing calls like "
            "join_words(['a','b','c']). Return the modified function.\n\n"
            "def join_words(words):\n    return ' '.join(words)"
        ),
        "checks": [
            "lambda: join_words(['a','b','c'])=='a b c'",
            "lambda: join_words([])==''",
            "lambda: join_words(['solo'])=='solo'",
            "lambda: join_words(['a','b','c'], sep='-')=='a-b-c'",
            "lambda: join_words(['x','y'], ',')=='x,y'",
        ],
    },
    "mod_clamp": {
        "kind": "modify",
        "prompt": (
            "Add optional parameters lo (default 0) and hi (default 100) to the function below so the "
            "clamp range is configurable, WITHOUT breaking existing calls like clamp(50) or clamp(150) "
            "that rely on the 0..100 default range. Return the modified function.\n\n"
            "def clamp(x):\n    if x < 0:\n        return 0\n    if x > 100:\n        return 100\n    return x"
        ),
        "checks": [
            "lambda: clamp(50)==50",
            "lambda: clamp(-5)==0",
            "lambda: clamp(150)==100",
            "lambda: clamp(5, lo=10, hi=20)==10",
            "lambda: clamp(25, 10, 20)==20",
        ],
    },
    "mod_get_field": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter default (default None) to the function below so that, when key "
            "is missing from record, default is returned instead of always returning None, WITHOUT "
            "breaking existing calls like get_field({'a':1},'a') or get_field({'a':1},'b'). Return the "
            "modified function.\n\n"
            "def get_field(record, key):\n    return record.get(key)"
        ),
        "checks": [
            "lambda: get_field({'a':1},'a')==1",
            "lambda: get_field({'a':1},'b') is None",
            "lambda: get_field({'a':1},'b',default=0)==0",
            "lambda: get_field({},'x','missing')=='missing'",
            "lambda: get_field({'a':1},'a',999)==1",
        ],
    },
    "mod_total": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter start (default 0) to the function below, matching the built-in "
            "sum()'s start argument, so the running total begins at start instead of always at 0, "
            "WITHOUT breaking existing calls like total([1,2,3]). Return the modified function.\n\n"
            "def total(nums):\n    s = 0\n    for n in nums:\n        s += n\n    return s"
        ),
        "checks": [
            "lambda: total([1,2,3])==6",
            "lambda: total([])==0",
            "lambda: total([1,2,3], start=10)==16",
            "lambda: total([], start=5)==5",
            "lambda: total([1,1,1], 100)==103",
        ],
    },
    "mod_greet": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter title (default None) to the function below so that, when title "
            "is truthy, it is inserted between 'Hello,' and name, WITHOUT breaking existing calls like "
            "greet('Alice') which must still return 'Hello, Alice!'. Return the modified function.\n\n"
            "def greet(name):\n    return f'Hello, {name}!'"
        ),
        "checks": [
            "lambda: greet('Alice')=='Hello, Alice!'",
            "lambda: greet('Bob')=='Hello, Bob!'",
            "lambda: greet('Alice', title='Dr.')=='Hello, Dr. Alice!'",
            "lambda: greet('Smith', 'Mr.')=='Hello, Mr. Smith!'",
            "lambda: greet('Eve', title=None)=='Hello, Eve!'",
        ],
    },
    "mod_words_longer_than": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter max_len (default None) to the function below so that, when "
            "max_len is given, only words with length strictly greater than n AND less than or equal to "
            "max_len are kept (when max_len is None, behave exactly as before: length strictly greater "
            "than n, no upper bound), WITHOUT breaking existing calls like "
            "words_longer_than(['a','bb','ccc','dddd'],2). Return the modified function.\n\n"
            "def words_longer_than(words, n):\n    return [w for w in words if len(w) > n]"
        ),
        "checks": [
            "lambda: words_longer_than(['a','bb','ccc','dddd'],2)==['ccc','dddd']",
            "lambda: words_longer_than([],3)==[]",
            "lambda: words_longer_than(['a','bb','ccc','dddd'],1,max_len=3)==['bb','ccc']",
            "lambda: words_longer_than(['x','yy','zzz'],0,2)==['x','yy']",
            "lambda: words_longer_than(['aa','bbb'],1,max_len=None)==['aa','bbb']",
        ],
    },
    "mod_stats": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter include_total (default False) to the function below so that, "
            "when include_total is True, the returned dict also has a 'total' key holding sum(nums), "
            "WITHOUT breaking existing calls like stats([1,2,3]) which must still return exactly "
            "{'min':1,'max':3,'avg':2.0} (no 'total' key). Return the modified function.\n\n"
            "def stats(nums):\n    return {'min': min(nums), 'max': max(nums), 'avg': sum(nums) / len(nums)}"
        ),
        "checks": [
            "lambda: stats([1,2,3])=={'min':1,'max':3,'avg':2.0}",
            "lambda: stats([5])=={'min':5,'max':5,'avg':5.0}",
            "lambda: 'total' not in stats([1,2,3])",
            "lambda: stats([1,2,3],include_total=True)=={'min':1,'max':3,'avg':2.0,'total':6}",
            "lambda: stats([2,4],True)=={'min':2,'max':4,'avg':3.0,'total':6}",
        ],
    },
    "mod_reverse_str": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter upper (default False) to the function below so that, when upper "
            "is True, the reversed string is uppercased, WITHOUT breaking existing calls like "
            "reverse_str('hello') which must still return 'olleh'. Return the modified function.\n\n"
            "def reverse_str(s):\n    return s[::-1]"
        ),
        "checks": [
            "lambda: reverse_str('hello')=='olleh'",
            "lambda: reverse_str('')==''",
            "lambda: reverse_str('abc')=='cba'",
            "lambda: reverse_str('hello', upper=True)=='OLLEH'",
            "lambda: reverse_str('abc', True)=='CBA'",
        ],
    },
    "mod_find_max_index": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter last (default False) to the function below so that, when last "
            "is True, it returns the index of the LAST occurrence of the maximum value instead of the "
            "first, WITHOUT breaking existing calls like find_max_index([1,3,3,2]) which must still "
            "return the index of the FIRST occurrence (1). Return the modified function.\n\n"
            "def find_max_index(nums):\n    m = max(nums)\n    return nums.index(m)"
        ),
        "checks": [
            "lambda: find_max_index([1,3,3,2])==1",
            "lambda: find_max_index([5])==0",
            "lambda: find_max_index([1,3,3,2], last=True)==2",
            "lambda: find_max_index([5,5,5], last=True)==2",
            "lambda: find_max_index([1,3,3,2], True)==2",
        ],
    },
    "mod_flatten_one": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter depth (default 1) to the function below so it flattens up to "
            "that many levels of nested lists (depth=1 keeps the current one-level-only behavior; a "
            "deeper level of nesting stays untouched at depth=1), WITHOUT breaking existing calls like "
            "flatten_one([1,[2,3],4]) or flatten_one([1,[2,[3,4]],5]) (the latter must still return "
            "[1,2,[3,4],5] when depth is omitted). Return the modified function.\n\n"
            "def flatten_one(lst):\n    out = []\n    for e in lst:\n        if isinstance(e, list):\n            out.extend(e)\n        else:\n            out.append(e)\n    return out"
        ),
        "checks": [
            "lambda: flatten_one([1,[2,3],4])==[1,2,3,4]",
            "lambda: flatten_one([[1,2],[3,4]])==[1,2,3,4]",
            "lambda: flatten_one([1,[2,[3,4]],5])==[1,2,[3,4],5]",
            "lambda: flatten_one([1,[2,[3,4]],5], depth=2)==[1,2,3,4,5]",
            "lambda: flatten_one([[[1]]], depth=3)==[1]",
        ],
    },
    "mod_count_words": {
        "kind": "modify",
        "prompt": (
            "Add an optional parameter min_len (default 0) to the function below so it only counts "
            "words with length at least min_len, WITHOUT breaking existing calls like "
            "count_words('the quick brown fox') which must still return 4. Return the modified "
            "function.\n\n"
            "def count_words(text):\n    return len(text.split())"
        ),
        "checks": [
            "lambda: count_words('the quick brown fox')==4",
            "lambda: count_words('')==0",
            "lambda: count_words('a bb ccc')==3",
            "lambda: count_words('a bb ccc', min_len=2)==2",
            "lambda: count_words('a bb ccc dddd', 3)==2",
        ],
    },
}

# The exact code shown in each task's prompt (the buggy or unmodified function). Must appear
# verbatim inside TASKS[name]["prompt"] — selftest() checks this so the "does it detect the bug"
# check is proven against exactly what the model is shown, not a paraphrase.
ORIGINAL = {
    "dbg_running_total": "def running_total(nums):\n    out = []\n    total = 0\n    for i in range(1, len(nums)):\n        total += nums[i]\n        out.append(total)\n    return out",
    "dbg_count_evens": "def count_evens(nums):\n    count = 0\n    for n in nums:\n        if n % 2 != 0:\n            count += 1\n    return count",
    "dbg_max_subarray_sum": "def max_subarray_sum(nums):\n    best = 0\n    cur = 0\n    for n in nums:\n        cur = cur + n\n        if cur < 0:\n            cur = 0\n        best = max(best, cur)\n    return best",
    "dbg_is_palindrome": "def is_palindrome(s):\n    return s == s[::-1]",
    "dbg_count_vowels": "def count_vowels(s):\n    count = 0\n    for c in s:\n        if c in 'aeiou':\n            count += 1\n    return count",
    "dbg_dedupe_sorted": "def dedupe_sorted(nums):\n    out = []\n    for i in range(len(nums) - 1):\n        if nums[i] != nums[i + 1]:\n            out.append(nums[i])\n    return out",
    "dbg_binary_to_int": "def binary_to_int(bits):\n    n = 0\n    for i, b in enumerate(bits):\n        n += int(b) * (2 ** i)\n    return n",
    "dbg_second_largest": "def second_largest(nums):\n    first = second = 0\n    for n in nums:\n        if n > first:\n            second = first\n            first = n\n        elif n > second:\n            second = n\n    return second",
    "dbg_append_to_all": "def append_to_all(item, lst=[]):\n    lst.append(item)\n    return lst",
    "dbg_chunk_list": "def chunk_list(lst, n):\n    size = len(lst) // n\n    return [lst[i * size:(i + 1) * size] for i in range(n)]",
    "dbg_word_freq": "def word_freq(text):\n    freq = {}\n    for w in text.lower().split():\n        freq[w] = 1\n    return freq",
    "dbg_transpose": "def transpose(m):\n    n = len(m)\n    return [[m[j][i] for j in range(n)] for i in range(n)]",
    "mod_keep_positive": "def keep_positive(nums):\n    return [n for n in nums if n > 0]",
    "mod_join_words": "def join_words(words):\n    return ' '.join(words)",
    "mod_clamp": "def clamp(x):\n    if x < 0:\n        return 0\n    if x > 100:\n        return 100\n    return x",
    "mod_get_field": "def get_field(record, key):\n    return record.get(key)",
    "mod_total": "def total(nums):\n    s = 0\n    for n in nums:\n        s += n\n    return s",
    "mod_greet": "def greet(name):\n    return f'Hello, {name}!'",
    "mod_words_longer_than": "def words_longer_than(words, n):\n    return [w for w in words if len(w) > n]",
    "mod_stats": "def stats(nums):\n    return {'min': min(nums), 'max': max(nums), 'avg': sum(nums) / len(nums)}",
    "mod_reverse_str": "def reverse_str(s):\n    return s[::-1]",
    "mod_find_max_index": "def find_max_index(nums):\n    m = max(nums)\n    return nums.index(m)",
    "mod_flatten_one": "def flatten_one(lst):\n    out = []\n    for e in lst:\n        if isinstance(e, list):\n            out.extend(e)\n        else:\n            out.append(e)\n    return out",
    "mod_count_words": "def count_words(text):\n    return len(text.split())",
}

REFERENCE = {
    "dbg_running_total": "def running_total(nums):\n    out = []\n    total = 0\n    for i in range(len(nums)):\n        total += nums[i]\n        out.append(total)\n    return out\n",
    "dbg_count_evens": "def count_evens(nums):\n    count = 0\n    for n in nums:\n        if n % 2 == 0:\n            count += 1\n    return count\n",
    "dbg_max_subarray_sum": "def max_subarray_sum(nums):\n    if not nums:\n        return 0\n    best = nums[0]\n    cur = nums[0]\n    for n in nums[1:]:\n        cur = max(n, cur + n)\n        best = max(best, cur)\n    return best\n",
    "dbg_is_palindrome": "def is_palindrome(s):\n    t = ''.join(c.lower() for c in s if c.isalnum())\n    return t == t[::-1]\n",
    "dbg_count_vowels": "def count_vowels(s):\n    count = 0\n    for c in s:\n        if c.lower() in 'aeiou':\n            count += 1\n    return count\n",
    "dbg_dedupe_sorted": "def dedupe_sorted(nums):\n    out = []\n    for i in range(len(nums)):\n        if i == 0 or nums[i] != nums[i - 1]:\n            out.append(nums[i])\n    return out\n",
    "dbg_binary_to_int": "def binary_to_int(bits):\n    n = 0\n    for b in bits:\n        n = n * 2 + int(b)\n    return n\n",
    "dbg_second_largest": "def second_largest(nums):\n    first = second = None\n    for n in nums:\n        if first is None or n > first:\n            second = first\n            first = n\n        elif n != first and (second is None or n > second):\n            second = n\n    return second\n",
    "dbg_append_to_all": "def append_to_all(item, lst=None):\n    if lst is None:\n        lst = []\n    lst.append(item)\n    return lst\n",
    "dbg_chunk_list": "def chunk_list(lst, n):\n    k, m = divmod(len(lst), n)\n    return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]\n",
    "dbg_word_freq": "def word_freq(text):\n    freq = {}\n    for w in text.lower().split():\n        freq[w] = freq.get(w, 0) + 1\n    return freq\n",
    "dbg_transpose": "def transpose(m):\n    if not m:\n        return []\n    rows = len(m)\n    cols = len(m[0])\n    return [[m[j][i] for j in range(rows)] for i in range(cols)]\n",
    "mod_keep_positive": "def keep_positive(nums, include_zero=False):\n    if include_zero:\n        return [n for n in nums if n >= 0]\n    return [n for n in nums if n > 0]\n",
    "mod_join_words": "def join_words(words, sep=' '):\n    return sep.join(words)\n",
    "mod_clamp": "def clamp(x, lo=0, hi=100):\n    if x < lo:\n        return lo\n    if x > hi:\n        return hi\n    return x\n",
    "mod_get_field": "def get_field(record, key, default=None):\n    return record.get(key, default)\n",
    "mod_total": "def total(nums, start=0):\n    s = start\n    for n in nums:\n        s += n\n    return s\n",
    "mod_greet": "def greet(name, title=None):\n    if title:\n        return f'Hello, {title} {name}!'\n    return f'Hello, {name}!'\n",
    "mod_words_longer_than": "def words_longer_than(words, n, max_len=None):\n    if max_len is None:\n        return [w for w in words if len(w) > n]\n    return [w for w in words if n < len(w) <= max_len]\n",
    "mod_stats": "def stats(nums, include_total=False):\n    d = {'min': min(nums), 'max': max(nums), 'avg': sum(nums) / len(nums)}\n    if include_total:\n        d['total'] = sum(nums)\n    return d\n",
    "mod_reverse_str": "def reverse_str(s, upper=False):\n    r = s[::-1]\n    return r.upper() if upper else r\n",
    "mod_find_max_index": "def find_max_index(nums, last=False):\n    m = max(nums)\n    if last:\n        for i in range(len(nums) - 1, -1, -1):\n            if nums[i] == m:\n                return i\n    return nums.index(m)\n",
    "mod_flatten_one": "def flatten_one(lst, depth=1):\n    if depth <= 0:\n        return list(lst)\n    out = []\n    for e in lst:\n        if isinstance(e, list):\n            out.extend(flatten_one(e, depth - 1))\n        else:\n            out.append(e)\n    return out\n",
    "mod_count_words": "def count_words(text, min_len=0):\n    return len([w for w in text.split() if len(w) >= min_len])\n",
}

# For every "modify" task: indices into that task's checks list which describe the OLD behavior — the
# ORIGINAL function must still pass all of these (that's the "existing calls keep working" contract).
REGRESSION = {
    "mod_keep_positive": [0, 1, 2],
    "mod_join_words": [0, 1, 2],
    "mod_clamp": [0, 1, 2],
    "mod_get_field": [0, 1],
    "mod_total": [0, 1],
    "mod_greet": [0, 1],
    "mod_words_longer_than": [0, 1],
    "mod_stats": [0, 1, 2],
    "mod_reverse_str": [0, 1, 2],
    "mod_find_max_index": [0, 1],
    "mod_flatten_one": [0, 1, 2],
    "mod_count_words": [0, 1, 2],
}


def _defined_names(code):
    """Top-level function names a code string defines, by static regex (no exec needed)."""
    return set(re.findall(r"^def (\w+)\(", code, re.M))


def selftest():
    """Returns a list of (task, reason) failures. Empty list means the instrument is sound:
    every check is correct on REFERENCE, and every ORIGINAL is provably distinguishable from it
    (for modify tasks: passes its REGRESSION checks but fails at least one other)."""
    bad = []

    task_names = set(TASKS)
    if task_names != set(REFERENCE):
        bad.append(("<all>", "TASKS/REFERENCE key mismatch: %s" % (task_names ^ set(REFERENCE))))
    if task_names != set(ORIGINAL):
        bad.append(("<all>", "TASKS/ORIGINAL key mismatch: %s" % (task_names ^ set(ORIGINAL))))

    modify_names = {n for n, t in TASKS.items() if t["kind"] == "modify"}
    if modify_names != set(REGRESSION):
        bad.append(("<all>", "modify tasks/REGRESSION key mismatch: %s" % (modify_names ^ set(REGRESSION))))

    # Names hard_tasks.py's REFERENCE solutions define, so we can prove no collision (item 6).
    try:
        import hard_tasks
        other_names = set()
        for code in hard_tasks.REFERENCE.values():
            other_names |= _defined_names(code)
    except Exception as e:
        bad.append(("<all>", "could not import hard_tasks.py to check for name collisions: %r" % e))
        other_names = set()

    for name, t in TASKS.items():
        if name not in REFERENCE or name not in ORIGINAL:
            continue  # already flagged above

        # (5) ORIGINAL must appear verbatim in the prompt shown to the model.
        if ORIGINAL[name] not in t["prompt"]:
            bad.append((name, "ORIGINAL text does not appear verbatim in the prompt"))

        # (2) every check must be exactly True on REFERENCE.
        ref_ns = {}
        try:
            exec(REFERENCE[name], ref_ns)
        except Exception as e:
            bad.append((name, "REFERENCE failed to exec: %r" % e))
            ref_ns = None

        ref_results = []
        if ref_ns is not None:
            for chk in t["checks"]:
                try:
                    ok = eval(chk, ref_ns)() is True
                except Exception as e:
                    ok = False
                    bad.append((name, "check fails on REFERENCE: %s -> %r" % (chk, e)))
                    ref_results.append(False)
                    continue
                ref_results.append(ok)
                if not ok:
                    bad.append((name, "check is not True on REFERENCE: %s" % chk))

        # (6) no REFERENCE function name may collide with one hard_tasks.py already defines.
        collisions = _defined_names(REFERENCE[name]) & other_names
        if collisions:
            bad.append((name, "REFERENCE defines name(s) already used in hard_tasks.py: %s" % collisions))

        # (3)/(4) run every check against ORIGINAL.
        orig_ns = {}
        try:
            exec(ORIGINAL[name], orig_ns)
            orig_exec_ok = True
        except Exception as e:
            bad.append((name, "ORIGINAL failed to exec: %r" % e))
            orig_exec_ok = False

        orig_results = []
        if orig_exec_ok:
            for chk in t["checks"]:
                try:
                    ok = eval(chk, orig_ns)() is True
                except Exception:
                    ok = False
                orig_results.append(ok)

            if all(orig_results):
                bad.append((name, "ORIGINAL passes ALL checks — the checks can't detect the bug/missing feature"))

            if t["kind"] == "modify":
                for idx in REGRESSION.get(name, []):
                    if idx >= len(orig_results) or not orig_results[idx]:
                        bad.append((name, "ORIGINAL fails a REGRESSION check at index %d" % idx))

    return bad


if __name__ == "__main__":
    bad = selftest()
    n_debug = sum(1 for t in TASKS.values() if t["kind"] == "debug")
    n_modify = sum(1 for t in TASKS.values() if t["kind"] == "modify")
    n_checks = sum(len(t["checks"]) for t in TASKS.values())
    print(
        "tasks: %d (debug %d / modify %d) | checks: %d | failures: %d"
        % (len(TASKS), n_debug, n_modify, n_checks, len(bad))
    )
    for name, reason in bad:
        print("  FAIL", name, "-", reason)
    raise SystemExit(1 if bad else 0)
