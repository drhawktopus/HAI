#!/usr/bin/env bash
# Async-RPC patch 5 correctness: the 1.5B split over two localhost rpc-servers must give CPU's perplexity (4.8875 on
# that machine) three ways - no server cache, cache cold (every weight a miss, sent with the cache flag), cache warm
# (every weight a hash hit, nothing re-sent). The cache lives in its own test directory, removed at the end.
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
B=$BUILD_HOME/llama.cpp-rpcasync/build-x86-c16/bin
M=$BUILD_HOME/models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf
T=$BUILD_HOME/llama.cpp-rpcasync/README.md
O=$BUILD_HOME/llama-build-rpcasync/ppl_check5
C=$BUILD_HOME/llama-build-rpcasync/ppl_check5/cache
rm -rf $O; mkdir -p $C
export LD_LIBRARY_PATH=$B
run() {   # $1 label, $2 server flags
  LLAMA_CACHE=$C $B/ggml-rpc-server -H 127.0.0.1 -p 50085 -t 2 $2 > $O/s1-$1.log 2>&1 < /dev/null & P1=$!
  LLAMA_CACHE=$C $B/ggml-rpc-server -H 127.0.0.1 -p 50086 -t 2 $2 > $O/s2-$1.log 2>&1 < /dev/null & P2=$!
  sleep 2
  t0=$(date +%s.%N)
  $B/llama-perplexity -v -m $M -f $T -c 512 -b 512 -ub 64 --chunks 4 -t 4 -ngl 99 \
    --rpc 127.0.0.1:50085,127.0.0.1:50086 -dev RPC0,RPC1 -ts 1,1 > $O/p-$1.log 2>&1
  rc=$?
  t1=$(date +%s.%N)
  kill $P1 $P2; wait $P1 $P2 2>/dev/null
  echo "== $1: rc=$rc wall $(echo "$t1 - $t0" | bc)s  $(grep -o 'Final estimate: PPL = [0-9.]*' $O/p-$1.log)  load: $(grep -o 'load time = *[0-9.]* ms' $O/p-$1.log | head -1)  cache files: $(ls $C/rpc 2>/dev/null | wc -l)"
  grep -c "offloaded 29/29" $O/p-$1.log | sed 's/^/   offloaded 29\/29 lines: /'
}
run nocache ""
run cold "-c"
run warm "-c"
echo "listeners left: $(ss -ltn | grep -cE ':5008[56] ')"
rm -rf $O/cache
echo DONE
