#!/bin/bash

command_output=$(sed 's/{{/YY/g ; s/}}/ZZ/g ; /frontend_base_url_val/d ; /task_secret_val/d ; /aws_region_val/d ; /docker_name_val/d ' <outpost_template.py | grep -e '}' -e '{' 2>&1)

templint_out=`pylint --disable=W0311,C0301,C0114,C0116,W0718,C0103,C0413,C0411,C0115,R0911,W0603,W1514,W0621,R0914,R0903,R0913,R1735,R1705,W0511,W1309,W0719,W0612,R0917 outpost_template.py`
templint_retcode=$?

# Check if the command_output variable is empty
if [ -z "$command_output" ]; then
  if [ "$templint_retcode" == 0 ]; then
    echo "pylint of outpost_template.py looks ok"
  else
    echo "! ! ! * * * ! ! ! BAD ! ! ! * * * ! ! !"
    echo "outpost_template.py failed pylint:"
    echo "$templint_out"
    exit 1
  fi
  echo "outpost_template.py template looks ok"
else
  echo "! ! ! * * * ! ! ! BAD ! ! ! * * * ! ! !"
  echo "Error: Isolated {, or } characters found in outpost_template.py:"
  echo "$command_output"
  exit 1
fi

lint_output=$(pylint --disable=C0301,C0114,C0103,C0116,R0914,W0718 ec2_launcher.py 2>&1)
lint_retcode=$?
if [ "$lint_retcode" == 0 ]; then
  echo "pylint of ec2_launcher.py looks ok"
else
  echo "! ! ! * * * ! ! ! BAD ! ! ! * * * ! ! !"
  echo "ec2_launcher.py failed pylint:"
  echo "$lint_output"
  exit 1
fi
