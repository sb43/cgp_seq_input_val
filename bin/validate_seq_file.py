#!/usr/bin/env python3

"""
Validates up to 2 sequencing data files
When 2 found these should be paired fastq[.gz],
otherwise expecting interleaved fastq.

May be extended to cover BAM/CRAM at a later date.
"""

# python builtin
import sys
import argparse

# this project
from cgp_seq_input_val.seq_validator import SeqValidator
from cgp_seq_input_val.seq_validator import SeqValidationError

parser = argparse.ArgumentParser(description="""Validates up to 2 sequencing data files.""")
parser.add_argument('-r', '--report', dest='report', type=argparse.FileType('w'), default='-',
                    help='Output json report', required=False)
parser.add_argument('-i', '--input', dest='input', metavar='FILE', nargs='+',
                    help='Input manifest in tsv formats', required=True)

args = parser.parse_args()

try:
    file_2 = None
    if len(args.input) == 2:
        file_2 = args.input[1]
    validator = SeqValidator(args.input[0], file_2)
    validator.validate()
    validator.report(args.report)
except SeqValidationError as ve:
    print("ERROR: " + str(ve), file=sys.stderr)
    exit(1)

# Interleaved fastq to paired:
# gnu-sed needed
# zcat 242215_i.fq.gz | gsed -n '1~8,+3p' | gzip -c > 242215_1.fq.gz
# zcat 242215_i.fq.gz | gsed -n '5~8,+3p' | gzip -c > 242215_2.fq.gz
