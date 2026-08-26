#!/bin/bash
# R8, the definitive form. strace is installed on this box, so stop inferring.
#
# Attaching strace for a whole rip is too heavy, and it does not need to be: the
# question is only whether MakeMKV goes back to rewrite MKV headers and Cues *at the
# end*. So idle until the output file stops growing, then attach for the finalisation
# window and record every seek and positional write.
#
#   usage: r8trace.sh <makemkvcon-pid> [quiet-seconds]
PID=$1
QUIET=${2:-6}
OUT=/tmp/r8trace.out
target=""
while [ -z "$target" ] && [ -d /proc/$PID ]; do
  target=$(ls -l /proc/$PID/fd 2>/dev/null | awk '/\.mkv$/{print $11; exit}')
  sleep 1
done
[ -z "$target" ] && { echo "R8: never saw a .mkv fd on pid $PID"; exit 1; }
echo "R8: watching $target"

last=0; still=0
while [ -d /proc/$PID ]; do
  sz=$(stat -c %s "$target" 2>/dev/null || echo 0)
  if [ "$sz" -eq "$last" ] && [ "$sz" -gt 0 ]; then
    still=$((still+1))
  else
    still=0
  fi
  last=$sz
  # The file has stopped growing: either finalisation is under way or it is imminent.
  if [ "$still" -ge "$QUIET" ]; then
    echo "R8: size held at $sz for ${QUIET}s -- attaching strace"
    timeout 180 strace -f -p $PID -e trace=lseek,pwrite64,pwritev,ftruncate,write \
        -o $OUT 2>/dev/null
    break
  fi
  sleep 1
done

echo "=== R8 finalisation trace ==="
[ -s $OUT ] || { echo "no syscalls captured"; exit 0; }
echo "total lines: $(wc -l < $OUT)"
echo "-- lseek to an absolute offset (SEEK_SET), which is what a rewrite looks like --"
grep -c "SEEK_SET" $OUT
echo "-- ftruncate --"
grep -c "ftruncate" $OUT
echo "-- first 40 seek/truncate lines --"
grep -E "lseek|ftruncate" $OUT | head -40
