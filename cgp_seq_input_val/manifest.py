"""
Functions and classes to support normalisation, reading and validation of
manifests.
"""
import os
import json
import re
import sys
import shutil
import uuid
from importlib import import_module

from cgp_seq_input_val import constants
from cgp_seq_input_val.error_classes import ConfigError, ParsingError, ValidationError
from cgp_seq_input_val.file_meta import FileMeta

def normalise(args):
    """
    Takes the arguments captured by the normalise_manifest.py executable
    and converts all formats to tsv.
    """
    # ALTERNATE SUCCESS PATHS in this block
    # Extensions are checked by argparse
    if args.input.endswith('tsv') is True:
        if args.output is None:
            print("\nINFO: input and output will be same file, no action required\n", file=sys.stderr)
            return True
        else:
            if os.path.exists(args.output):
                if os.path.samefile(args.input, args.output):
                    print("\nINFO: input and output point to the same file, no action required", file=sys.stderr)
                    return True
            # anything else is a copy
            print("\nINFO: input copied to output, no format conversion required.", file=sys.stderr)
            shutil.copyfile(args.input, args.output, follow_symlinks=True)
            return True

    if args.input.endswith('tsv') is False and args.output is None:
        args.output = re.sub(r'\.[^.]+$', '.tsv', args.input)

    manifest = Manifest(args.input)
    manifest.convert_by_extn(args.output)

class Manifest(object):
    """
    Top level object used to validate a manifest TSV file.
    This runs validation of the header and body in turn rasing execptions as appropriate.
    Configuration is handled via the json files found in the config sub directory.
    """
    def __init__(self, infile):
        self.infile = infile
        self.informat = os.path.splitext(infile)[1][1:]
        self.header = None
        self.config = None
        self.body = None

    def _xlsx_to_tsv(self, ofh):
        self._excel_to_tsv(ofh)

    def _xls_to_tsv(self, ofh):
        self._excel_to_tsv(ofh)

    def _csv_to_tsv(self, ofh):
        csv = import_module('csv')
        with open(self.infile, 'r') as csvfh:
            for row in csv.reader(csvfh, delimiter=','):
                joined = "\t".join(row)
                if not joined.startswith('\t'):
                    print(joined, file=ofh)

    def _excel_to_tsv(self, ofh):
        xlrd = import_module('xlrd')
        book = xlrd.open_workbook(self.infile, formatting_info=False, on_demand=True, ragged_rows=True)
        sheet = book.sheet_by_name('For entry')
        for r in range(0, sheet.nrows):
            simplerow = []
            cols = sheet.row_len(r)
            # do some cleanup
            if cols == 0 or sheet.cell_value(r, 0) == '':
                continue
            for c in range(0, cols):
                simplerow.append(str(sheet.cell_value(r, c)))
            print("\t".join(simplerow), file=ofh)

    def convert_by_extn(self, outfile):
        """Uses the input file extension to determine the correct file conversion
        routine.  Output is always tsv file.  Expects the output file name extension
        to have been checked in advance.
        """
        with open(outfile, 'w') as ofh:
            convertor = getattr(self, '_' + self.informat + '_to_tsv')
            convertor(ofh)

    def validate(self, checkFiles=False):
        """
        Runs the actual validation of a manifest:
         - Create header object
         - Load config
         - Validate header
         - Create body object
         - Validate body
        """
        if self.informat != 'tsv':
            raise ValueError('Manifest.validate only accepts files of type "tsv"')
        # Generate the header object
        self.header = Header(self.infile)
        self.config = self.header.get_config()
        self.header.validate(self.config['header'])
        # process body of document
        self.body = Body(self.infile, self.config['body'])
        self.body.validate(self.config['body'])
        if checkFiles:
            self.body.file_tests()

    def write(self, outdir):
        """
        Generate the new tsv file with added UUID info and
        the json representation for use later.
        """
        tsv_file = os.path.join(outdir, self.header.items['Our Ref:'] + '.tsv')
        for_json = {'header': None, 'body': None}
        with open(tsv_file, 'w') as fp:
            for_json['header'] = self.header.write(fp)
            for_json['body'] = self.body.write(fp, self.config['body'])

        js_file = re.sub(r'tsv$', 'json', tsv_file)
        with open(js_file, 'w') as fp:
            json.dump(for_json, fp, sort_keys=True, indent=4)
        return tsv_file, js_file

class Header(object):
    """
    Object to load and validate the header section of a manifest
    """
    def __init__(self, manifest):
        self.manifest = manifest
        csv = import_module('csv')
        header_items = {}
        with open(self.manifest, 'r') as ifh:
            for row in csv.reader(ifh, delimiter='\t'):
                if row[0] == constants.HEADER_BODY_SWITCH:
                    break
                val = ''
                if len(row) > 1:
                    val = row[1]
                header_items[row[0]] = val

        # now load the ini based on 'Form type:' and 'Form version:'
        self.type = header_items['Form type:']
        self.version = header_items['Form version:']
        self._all_items = header_items
        self.items = {}

    def write(self, fp):
        """
        Writes the header to a file-pointer in tsv and returns the values
        needed in the json object.
        """
        for key, val in self.items.items():
            print("%s\t%s" % (key, val), file=fp)

        print("%s\t%s" % ('Form type:', self.type), file=fp)
        print("%s\t%s" % ('Form version:', self.version), file=fp)

        return self.items

    def get_config(self, cfg_file=None):
        """
        Return the location of the config file to use in validation steps
        """
        if cfg_file is None:
            cfg_file = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                    'config',
                                    '%s-%s.json' % (self.type, self.version))
        with open(cfg_file, 'r') as j:
            config = json.load(j)

        if config['type'] != self.type:
            raise ParsingError("Filename (%s) does not match 'type' (%s) within file"
                               % (cfg_file, config['type']))
        if config['version'] != self.version:
            raise ParsingError("Filename (%s) does not match 'version' (%s) within file"
                               % (cfg_file, config['version']))

        self.validate_json(config)
        return config

    def validate_json(self, config):
        """
        Check the config is valid, simple set of rules:
        1. 'header' exists:
          - 'expected', 'required' and 'validate' must exist.
          - 'validate' keys must exist in 'required' list.
          - 'required' list must exist in 'expected' list.
        2. 'body' exists:
          - body content validated by it's own class.
        """
        if 'header' not in config:
            raise ConfigError("header (dict/hash) not found in json file: %s-%s.json"
                              % (self.type, self.version))
        if 'expected' not in config['header']:
            raise ConfigError("header.expected (list/array) not found in json file: %s-%s.json"
                              % (self.type, self.version))
        if 'required' not in config['header']:
            raise ConfigError("header.required (list/array) not found in json file: %s-%s.json"
                              % (self.type, self.version))
        if 'validate' not in config['header']:
            raise ConfigError("header.validate (dict/hash) not found in json file: %s-%s.json"
                              % (self.type, self.version))
        if 'body' not in config:
            raise ConfigError("body (dict/hash) not found in json file: %s-%s.json"
                              % (self.type, self.version))

    def fields_exist(self, expected):
        """
        Checks all field that are expected to exist in the header of this type+version
        of the manifest.  It is not checking for values, just the expected elements.
        These are detailed in the header.expected element of the json file.
        Adds these to the 'items' dict of the header object.
        """
        found = set(self._all_items.keys())
        expected_fields = set(expected)
        unexpected = found.difference(expected_fields)
        if unexpected: # empty sequences are false, don't use "len() > 0"
            raise ValidationError("The following unexpected fields were found in the header of your file:\n\t'"
                                  + "'\n\t'".join(unexpected) + "'")
        missing_fields = expected_fields.difference(found)
        if missing_fields:
            raise ValidationError("The following expected fields were missing from the header of your file:\n\t'"
                                  + "'\n\t'".join(missing_fields) + "'")
        # add the elements to the approved header items dict
        for key, val in self._all_items.items():
            if key in ('Form type:', 'Form version:'):
                continue
            self.items[key] = val

    def fields_have_values(self, required):
        """
        Check all fields that should have values do for this type+version.
        These are detailed in the header.required element of the json file.
        """
        for item in required:
            if not self.items[item]: # empty sequences are false, don't use "len() == 0"
                raise ValidationError("Header item '%s' has no value." % (item))

    def field_values_valid(self, validate):
        """
        Checks all restricted fields have valid values for this type+version.
        """
        for item in validate:
            if self.items[item] not in validate[item]:
                raise ValidationError("Header item '%s' has an invalid value of: %s" % (item, self.items[item]))

    def validate(self, rules):
        """
        Runs the different elements of header validation:
         - fields_exist
         - fields_have_values
         - field_values_valid
        """
        self.fields_exist(rules['expected'])
        self.fields_have_values(rules['required'])
        self.field_values_valid(rules['validate'])

        if self.items['Our Ref:'] == '':
            # add UUID unless it exists
            self.items['Our Ref:'] = str(uuid.uuid4())

class Body(object):
    """
    Body object validates the individual records of a manifest.
    Takes the body component of the config object loaded/checked
    by header object.
    """
    def __init__(self, manifest, config):
        self.manifest = manifest
        self.offset = 1 # start at one as would need to increment for header line otherwise
        manifest_dir = os.path.dirname(manifest)
        csv = import_module('csv')
        self.file_detail = []
        loadRows = False
        with open(self.manifest, 'r') as ifh:
            for row in csv.reader(ifh, delimiter='\t'):
                if row[0] == constants.HEADER_BODY_SWITCH:
                    loadRows = True
                    self.headings = row
                    self.heading_check(config)
                    continue
                if not loadRows:
                    self.offset += 1
                    continue
                self.file_detail.append(FileMeta(self.headings, row, manifest_dir))

    def write(self, fp, config):
        """
        Writes the body to a file-pointer in tsv and returns the values
        needed in the json object.
        """
        for_json = []
        ordered = config['ordered']
        print("\t".join(ordered), file=fp)
        for fd in self.file_detail:
            row = []
            for_json.append(fd.attributes)
            for col in ordered:
                row.append(fd.attributes[col])
            print("\t".join(row), file=fp)
        return for_json


    def validate(self, rules):
        """
        Runs the different elements of body validation:
         - validate fields with restriced dict
         - validate file/file_2 do not overlap
        """
        self.fields_have_values(rules['required'])
        self.field_values_valid(rules['validate'])
        self.uniq_files()
        self.file_ext_check(rules['validate_ext'])

    def field_values_valid(self, validate):
        """
        Check fields with restriced dict are valid
        Must run after self.fields_have_values()
        """
        for item in validate:
            cnt = self.offset
            for fd in self.file_detail:
                cnt += 1
                if fd.attributes[item] not in validate[item]:
                    raise ValidationError("Metadata item '%s' has an invalid value of '%s' on line %d"
                                          % (item, fd.attributes[item], cnt))

    def fields_have_values(self, rules):
        """
        Check the fields listed as required are populated
        """
        cnt = self.offset
        for fd in self.file_detail:
            cnt += 1
            for req in rules:
                if (not fd.attributes[req]) or fd.attributes[req] == '.':
                    raise ValidationError("Required metadata value absent for '%s' on line %d ('.' not acceptable)"
                                          % (req, cnt))

    def uniq_files(self):
        """
        Check all filenames are uniq within this manifest
        """
        cnt = self.offset
        all_files = []
        for fd in self.file_detail:
            cnt += 1
            for f_type in ('File', 'File_2'):
                item = fd.attributes[f_type]
                if item == '.':
                    continue
                if item in all_files:
                    raise ValidationError("Metadata item '%s' has a duplicate value of '%s' on line %d"
                                          % (f_type, item, cnt))
                all_files.append(item)

    def file_ext_check(self, rules):
        """
        Check all files have valid extentions
        - see config/*.json
        """
        cnt = self.offset
        for fd in self.file_detail:
            cnt += 1
            last_ext = None
            for f_type in ('File', 'File_2'):
                item = fd.attributes[f_type]
                if item == '.':
                    continue
                extra = ''
                (base, ext) = os.path.splitext(item)
                if ext == '.gz':
                    extra = ext
                    (base, ext) = os.path.splitext(base)
                full_ext = ext + extra

                if full_ext not in rules[f_type]:
                    raise ValidationError("File extension of '%s' is not valid, '%s' on line %d"
                                          % (full_ext, f_type, cnt))

                if last_ext is not None and last_ext != full_ext:
                    raise ValidationError("File extensions for same row must match, '%s' vs '%s' on line %d"
                                          % (last_ext, full_ext, cnt))
                last_ext = full_ext


    def heading_check(self, config):
        """
        Simple check for correct, ordered headings for file rows.
        Here to minimise complexity of init
        """
        if self.headings != config['ordered']:
            raise ValidationError("Expected row headings of\n\t"
                                  + ', '.join(config['ordered'])
                                  + "\nbut got\n\t"
                                  + ', '.join(self.headings))

    def file_tests(self):
        """
        Test for file existance and content
        """
        cnt = self.offset
        for fd in self.file_detail:
            cnt += 1
            fd.test_files(cnt)
