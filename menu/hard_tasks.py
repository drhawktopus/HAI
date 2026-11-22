"""Hard Bench — coding task pool (candidate set for calibration), with reference solutions.

Each task: kind (impl/debug/modify), prompt, checks (lambda strings graded in a bounded child process by the
payload, exactly like map_swarm_code.py). REFERENCE holds a known-good solution per task; `selftest()` proves
every check is satisfiable and correct BEFORE any tablet time is spent (the instrument's positive control).
Difficulty is meant to spread from moderate to hard for a 1.5B coder so the 0.3–0.7 band has members.
"""

TASKS = {
    "roman_to_int": {
        "kind": "impl",
        "prompt": "Write a Python function roman_to_int(s) that converts a Roman numeral string (uppercase, valid, 1..3999) to an integer. Subtractive pairs (IV, IX, XL, XC, CD, CM) must be handled.",
        "checks": [
            "lambda: roman_to_int('III')==3",
            "lambda: roman_to_int('IV')==4",
            "lambda: roman_to_int('LVIII')==58",
            "lambda: roman_to_int('MCMXCIV')==1994",
            "lambda: roman_to_int('MMMCMXCIX')==3999",
        ],
    },
    "int_to_roman": {
        "kind": "impl",
        "prompt": "Write a Python function int_to_roman(n) that converts an integer 1..3999 to its uppercase Roman numeral string, using subtractive notation (4 is 'IV', 9 is 'IX', 40 is 'XL', 90 is 'XC', 400 is 'CD', 900 is 'CM').",
        "checks": [
            "lambda: int_to_roman(3)=='III'",
            "lambda: int_to_roman(4)=='IV'",
            "lambda: int_to_roman(58)=='LVIII'",
            "lambda: int_to_roman(1994)=='MCMXCIV'",
            "lambda: int_to_roman(3999)=='MMMCMXCIX'",
        ],
    },
    "rle_encode": {
        "kind": "impl",
        "prompt": "Write a Python function rle_encode(s) that run-length encodes a string: each maximal run of the same character becomes the character followed by the run length. Example: rle_encode('aaabcc') returns 'a3b1c2'. An empty string returns ''.",
        "checks": [
            "lambda: rle_encode('aaabcc')=='a3b1c2'",
            "lambda: rle_encode('')==''",
            "lambda: rle_encode('a')=='a1'",
            "lambda: rle_encode('abab')=='a1b1a1b1'",
            "lambda: rle_encode('zzzzzzzzzzzz')=='z12'",
        ],
    },
    "rle_decode": {
        "kind": "impl",
        "prompt": "Write a Python function rle_decode(s) that reverses run-length encoding where each character is followed by its run length (the length may have more than one digit). Example: rle_decode('a3b1c2') returns 'aaabcc'. An empty string returns ''.",
        "checks": [
            "lambda: rle_decode('a3b1c2')=='aaabcc'",
            "lambda: rle_decode('')==''",
            "lambda: rle_decode('z12')=='zzzzzzzzzzzz'",
            "lambda: rle_decode('a1b1a1b1')=='abab'",
            "lambda: rle_decode('x10y2')=='xxxxxxxxxxyy'",
        ],
    },
    "balanced_brackets": {
        "kind": "impl",
        "prompt": "Write a Python function balanced(s) that returns True if every bracket in s is closed by the matching kind in the correct order, considering only the characters ()[]{} (all other characters are ignored). Return False otherwise.",
        "checks": [
            "lambda: balanced('()[]{}') is True",
            "lambda: balanced('([{}])') is True",
            "lambda: balanced('(]') is False",
            "lambda: balanced('([)]') is False",
            "lambda: balanced('a(b)c') is True",
            "lambda: balanced('((') is False",
        ],
    },
    "merge_intervals": {
        "kind": "impl",
        "prompt": "Write a Python function merge_intervals(iv) that takes a list of [start, end] integer intervals in any order and returns a new list of merged, non-overlapping intervals sorted by start. Intervals that touch (end of one equals start of the next) are merged too.",
        "checks": [
            "lambda: merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]]",
            "lambda: merge_intervals([[1,4],[4,5]])==[[1,5]]",
            "lambda: merge_intervals([])==[]",
            "lambda: merge_intervals([[5,7],[1,3]])==[[1,3],[5,7]]",
            "lambda: merge_intervals([[1,10],[2,3],[4,5]])==[[1,10]]",
        ],
    },
    "longest_common_prefix": {
        "kind": "impl",
        "prompt": "Write a Python function lcp(words) that returns the longest string that is a prefix of every string in the list words. Return '' if there is no common prefix or the list is empty.",
        "checks": [
            "lambda: lcp(['flower','flow','flight'])=='fl'",
            "lambda: lcp(['dog','racecar','car'])==''",
            "lambda: lcp([])==''",
            "lambda: lcp(['same','same'])=='same'",
            "lambda: lcp(['a'])=='a'",
        ],
    },
    "top_k_words": {
        "kind": "impl",
        "prompt": "Write a Python function top_k(words, k) that returns the k most frequent strings in the list words, ordered by frequency descending and then alphabetically ascending for ties.",
        "checks": [
            "lambda: top_k(['i','love','leetcode','i','love','coding'],2)==['i','love']",
            "lambda: top_k(['the','day','is','sunny','the','the','the','sunny','is','is'],4)==['the','is','sunny','day']",
            "lambda: top_k(['b','a'],2)==['a','b']",
            "lambda: top_k(['x'],1)==['x']",
        ],
    },
    "rotate_matrix": {
        "kind": "impl",
        "prompt": "Write a Python function rotate(m) that returns a NEW square matrix (list of lists) equal to m rotated 90 degrees clockwise. Do not modify m.",
        "checks": [
            "lambda: rotate([[1,2],[3,4]])==[[3,1],[4,2]]",
            "lambda: rotate([[1,2,3],[4,5,6],[7,8,9]])==[[7,4,1],[8,5,2],[9,6,3]]",
            "lambda: rotate([[1]])==[[1]]",
            "lambda: (lambda m: (rotate(m), m)[1])([[1,2],[3,4]])==[[1,2],[3,4]]",
        ],
    },
    "valid_ipv4_v2": {
        "kind": "impl",
        "prompt": "Write a Python function valid_ipv4(s) that returns True only if s is a valid dotted-decimal IPv4 address: exactly four parts separated by single dots, each part is 1-3 digits, has no leading zero unless it is exactly '0', and is between 0 and 255.",
        "checks": [
            "lambda: valid_ipv4('10.0.0.1') is True",
            "lambda: valid_ipv4('256.1.1.1') is False",
            "lambda: valid_ipv4('01.1.1.1') is False",
            "lambda: valid_ipv4('1.1.1') is False",
            "lambda: valid_ipv4('0.0.0.0') is True",
            "lambda: valid_ipv4('1.1.1.1.') is False",
            "lambda: valid_ipv4('a.b.c.d') is False",
        ],
    },
    "caesar": {
        "kind": "impl",
        "prompt": "Write a Python function caesar(s, k) that shifts every ASCII letter in s forward by k positions in the alphabet, wrapping around, preserving case. Non-letters are unchanged. k may be negative or larger than 26.",
        "checks": [
            "lambda: caesar('abc',1)=='bcd'",
            "lambda: caesar('xyz',3)=='abc'",
            "lambda: caesar('Hello, World!',5)=='Mjqqt, Btwqi!'",
            "lambda: caesar('abc',-1)=='zab'",
            "lambda: caesar('abc',27)=='bcd'",
        ],
    },
    "group_anagrams": {
        "kind": "impl",
        "prompt": "Write a Python function group_anagrams(words) that groups words that are anagrams of each other. Return a list of groups; each group is a sorted list of words, and the groups are sorted by their first word.",
        "checks": [
            "lambda: group_anagrams(['eat','tea','tan','ate','nat','bat'])==[['ate','eat','tea'],['bat'],['nat','tan']]",
            "lambda: group_anagrams([])==[]",
            "lambda: group_anagrams(['a'])==[['a']]",
            "lambda: group_anagrams(['ab','ba','abc'])==[['ab','ba'],['abc']]",
        ],
    },
    "flatten": {
        "kind": "impl",
        "prompt": "Write a Python function flatten(x) that takes a list whose elements are integers or (arbitrarily nested) lists of integers and returns a flat list of the integers in order.",
        "checks": [
            "lambda: flatten([1,[2,[3,[4]]],5])==[1,2,3,4,5]",
            "lambda: flatten([])==[]",
            "lambda: flatten([[[]]])==[]",
            "lambda: flatten([1,2,3])==[1,2,3]",
            "lambda: flatten([[1],[2,[3]],[],[[4,5]]])==[1,2,3,4,5]",
        ],
    },
    "search_insert": {
        "kind": "impl",
        "prompt": "Write a Python function search_insert(nums, target) that, given a sorted list of distinct integers, returns the index of target if present, otherwise the index where it would be inserted to keep the list sorted. Use binary search (O(log n)).",
        "checks": [
            "lambda: search_insert([1,3,5,6],5)==2",
            "lambda: search_insert([1,3,5,6],2)==1",
            "lambda: search_insert([1,3,5,6],7)==4",
            "lambda: search_insert([1,3,5,6],0)==0",
            "lambda: search_insert([],3)==0",
        ],
    },
    "eval_rpn": {
        "kind": "impl",
        "prompt": "Write a Python function eval_rpn(tokens) that evaluates an expression in Reverse Polish Notation given as a list of string tokens. Operators are + - * / with integer operands; division truncates toward zero. Return an int.",
        "checks": [
            "lambda: eval_rpn(['2','1','+','3','*'])==9",
            "lambda: eval_rpn(['4','13','5','/','+'])==6",
            "lambda: eval_rpn(['10','6','9','3','+','-11','*','/','*','17','+','5','+'])==22",
            "lambda: eval_rpn(['7','-2','/'])==-3",
            "lambda: eval_rpn(['3'])==3",
        ],
    },
    "parse_csv_line": {
        "kind": "impl",
        "prompt": "Write a Python function parse_csv_line(line) that splits one CSV line into a list of field strings. Fields are separated by commas; a field may be wrapped in double quotes, in which case commas inside it do not split, and a doubled quote (\"\") inside a quoted field means one literal quote. Do not use the csv module.",
        "checks": [
            "lambda: parse_csv_line('a,b,c')==['a','b','c']",
            "lambda: parse_csv_line('\"x,y\",z')==['x,y','z']",
            "lambda: parse_csv_line('\"he said \"\"hi\"\"\",ok')==['he said \"hi\"','ok']",
            "lambda: parse_csv_line('')==['']",
            "lambda: parse_csv_line('a,,b')==['a','','b']",
        ],
    },
    "word_wrap": {
        "kind": "impl",
        "prompt": "Write a Python function wrap(text, width) that greedily wraps text into lines no longer than width characters, breaking only at spaces (words are never split; a single word longer than width goes on its own line). Return the list of lines with no leading or trailing spaces.",
        "checks": [
            "lambda: wrap('the quick brown fox',10)==['the quick','brown fox']",
            "lambda: wrap('a b c',1)==['a','b','c']",
            "lambda: wrap('',10)==[]",
            "lambda: wrap('supercalifragilistic is long',5)==['supercalifragilistic','is','long']",
            "lambda: wrap('one two',7)==['one two']",
        ],
    },
    "fix_binary_search": {
        "kind": "debug",
        "prompt": "The binary search below sometimes loops forever or misses the target. Return the corrected function, which must return the index of target in the sorted list nums, or -1 if absent.\n\ndef bsearch(nums, target):\n    lo, hi = 0, len(nums)\n    while lo < hi:\n        mid = (lo + hi) // 2\n        if nums[mid] < target:\n            lo = mid\n        elif nums[mid] > target:\n            hi = mid\n        else:\n            return mid\n    return -1",
        "checks": [
            "lambda: bsearch([1,3,5,7,9],7)==3",
            "lambda: bsearch([1,3,5,7,9],1)==0",
            "lambda: bsearch([1,3,5,7,9],9)==4",
            "lambda: bsearch([1,3,5,7,9],4)==-1",
            "lambda: bsearch([],4)==-1",
        ],
    },
    "fix_dedupe_order": {
        "kind": "debug",
        "prompt": "The function below is supposed to remove duplicate items from a list while KEEPING the first occurrence of each item in its original order, but it scrambles the order. Return the corrected function.\n\ndef dedupe(items):\n    return list(set(items))",
        "checks": [
            "lambda: dedupe([3,1,3,2,1])==[3,1,2]",
            "lambda: dedupe([])==[]",
            "lambda: dedupe(['b','a','b'])==['b','a']",
            "lambda: dedupe([1,1,1])==[1]",
        ],
    },
    "add_reverse_param": {
        "kind": "modify",
        "prompt": "Add an optional parameter reverse (default False) to the function below so that, when reverse is True, the result is sorted from largest to smallest, WITHOUT breaking existing calls like sort_nums([3,1,2]). Return the modified function.\n\ndef sort_nums(nums):\n    return sorted(nums)",
        "checks": [
            "lambda: sort_nums([3,1,2])==[1,2,3]",
            "lambda: sort_nums([3,1,2],reverse=True)==[3,2,1]",
            "lambda: sort_nums([3,1,2],True)==[3,2,1]",
            "lambda: sort_nums([])==[]",
        ],
    },
    "fix_average_empty": {
        "kind": "debug",
        "prompt": "The function below crashes on an empty list and returns an integer-truncated average for integer input. Return the corrected function: it must return the arithmetic mean as a float, and return 0.0 for an empty list.\n\ndef average(nums):\n    return sum(nums) // len(nums)",
        "checks": [
            "lambda: average([1,2,3,4])==2.5",
            "lambda: average([])==0.0",
            "lambda: isinstance(average([2,2]),float)",
            "lambda: average([5])==5.0",
        ],
    },
}

REFERENCE = {
    "roman_to_int": "def roman_to_int(s):\n    v={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}\n    t=0\n    for i,c in enumerate(s):\n        if i+1<len(s) and v[c]<v[s[i+1]]: t-=v[c]\n        else: t+=v[c]\n    return t\n",
    "int_to_roman": "def int_to_roman(n):\n    t=[(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]\n    out=''\n    for v,r in t:\n        while n>=v: out+=r; n-=v\n    return out\n",
    "rle_encode": "def rle_encode(s):\n    out=''; i=0\n    while i<len(s):\n        j=i\n        while j<len(s) and s[j]==s[i]: j+=1\n        out+=s[i]+str(j-i); i=j\n    return out\n",
    "rle_decode": "import re\ndef rle_decode(s):\n    return ''.join(c*int(n) for c,n in re.findall(r'(\\D)(\\d+)',s))\n",
    "balanced_brackets": "def balanced(s):\n    st=[]; pairs={')':'(',']':'[','}':'{'}\n    for c in s:\n        if c in '([{': st.append(c)\n        elif c in pairs:\n            if not st or st.pop()!=pairs[c]: return False\n    return not st\n",
    "merge_intervals": "def merge_intervals(iv):\n    out=[]\n    for a,b in sorted(iv):\n        if out and a<=out[-1][1]: out[-1][1]=max(out[-1][1],b)\n        else: out.append([a,b])\n    return out\n",
    "longest_common_prefix": "def lcp(words):\n    if not words: return ''\n    p=words[0]\n    for w in words[1:]:\n        while not w.startswith(p): p=p[:-1]\n    return p\n",
    "top_k_words": "def top_k(words,k):\n    from collections import Counter\n    c=Counter(words)\n    return [w for w,_ in sorted(c.items(), key=lambda kv:(-kv[1],kv[0]))[:k]]\n",
    "rotate_matrix": "def rotate(m):\n    n=len(m)\n    return [[m[n-1-j][i] for j in range(n)] for i in range(n)]\n",
    "valid_ipv4_v2": "def valid_ipv4(s):\n    parts=s.split('.')\n    if len(parts)!=4: return False\n    for p in parts:\n        if not p.isdigit() or len(p)>3: return False\n        if len(p)>1 and p[0]=='0': return False\n        if int(p)>255: return False\n    return True\n",
    "caesar": "def caesar(s,k):\n    out=''\n    for c in s:\n        if 'a'<=c<='z': out+=chr((ord(c)-97+k)%26+97)\n        elif 'A'<=c<='Z': out+=chr((ord(c)-65+k)%26+65)\n        else: out+=c\n    return out\n",
    "group_anagrams": "def group_anagrams(words):\n    g={}\n    for w in words: g.setdefault(''.join(sorted(w)),[]).append(w)\n    return sorted([sorted(v) for v in g.values()])\n",
    "flatten": "def flatten(x):\n    out=[]\n    for e in x:\n        if isinstance(e,list): out.extend(flatten(e))\n        else: out.append(e)\n    return out\n",
    "search_insert": "def search_insert(nums,target):\n    lo,hi=0,len(nums)\n    while lo<hi:\n        mid=(lo+hi)//2\n        if nums[mid]<target: lo=mid+1\n        else: hi=mid\n    return lo\n",
    "eval_rpn": "def eval_rpn(tokens):\n    st=[]\n    for t in tokens:\n        if t in '+-*/' and len(t)==1:\n            b=st.pop(); a=st.pop()\n            if t=='+': st.append(a+b)\n            elif t=='-': st.append(a-b)\n            elif t=='*': st.append(a*b)\n            else: st.append(int(a/b))\n        else: st.append(int(t))\n    return st[-1]\n",
    "parse_csv_line": "def parse_csv_line(line):\n    out=[]; cur=''; q=False; i=0\n    while i<len(line):\n        c=line[i]\n        if q:\n            if c=='\"':\n                if i+1<len(line) and line[i+1]=='\"': cur+='\"'; i+=1\n                else: q=False\n            else: cur+=c\n        else:\n            if c=='\"': q=True\n            elif c==',': out.append(cur); cur=''\n            else: cur+=c\n        i+=1\n    out.append(cur)\n    return out\n",
    "word_wrap": "def wrap(text,width):\n    lines=[]; cur=''\n    for w in text.split():\n        if not cur: cur=w\n        elif len(cur)+1+len(w)<=width: cur+=' '+w\n        else: lines.append(cur); cur=w\n    if cur: lines.append(cur)\n    return lines\n",
    "fix_binary_search": "def bsearch(nums,target):\n    lo,hi=0,len(nums)\n    while lo<hi:\n        mid=(lo+hi)//2\n        if nums[mid]<target: lo=mid+1\n        elif nums[mid]>target: hi=mid\n        else: return mid\n    return -1\n",
    "fix_dedupe_order": "def dedupe(items):\n    seen=set(); out=[]\n    for x in items:\n        if x not in seen: seen.add(x); out.append(x)\n    return out\n",
    "add_reverse_param": "def sort_nums(nums, reverse=False):\n    return sorted(nums, reverse=reverse)\n",
    "fix_average_empty": "def average(nums):\n    return sum(nums)/len(nums) if nums else 0.0\n",
}


def selftest():
    """Every check must pass on its reference (in-process; the payload grades in a child process)."""
    assert set(TASKS) == set(REFERENCE), "tasks without references: %s" % (set(TASKS) ^ set(REFERENCE))
    bad = []
    for name, t in TASKS.items():
        ns = {}
        exec(REFERENCE[name], ns)
        for chk in t["checks"]:
            try:
                ok = eval(chk, ns)() is True
            except Exception as e:
                ok = False
                chk = "%s -> %r" % (chk, e)
            if not ok:
                bad.append((name, chk))
    return bad


if __name__ == "__main__":
    bad = selftest()
    print("tasks:", len(TASKS), "| checks:", sum(len(t["checks"]) for t in TASKS.values()), "| failing on reference:", len(bad))
    for b in bad:
        print("  FAIL", b)
    raise SystemExit(1 if bad else 0)
