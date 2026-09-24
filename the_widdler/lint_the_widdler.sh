#!/bin/bash

templint_out=`pylint --disable=W0311,C0301,C0114,C0116,W0718,C0103,C0413,C0411,C0115,R0911,W0603,W1514,W0621,R0914,R0903,R0913,R1735,R1705,W0511,W1309,W0719,W0612,C0123,R0902,R0904,R0912,W0201,C0321,C0302,R0917,R0801 *.py`
templint_retcode=$?
if [ "$templint_retcode" == 0 ]; then
  echo "pylint of the widdler looks ok"
else
  echo "! ! ! * * * ! ! ! BAD ! ! ! * * * ! ! !"
  echo "the widdler failed pylint:"
  echo "$templint_out"
  exit 1
fi
