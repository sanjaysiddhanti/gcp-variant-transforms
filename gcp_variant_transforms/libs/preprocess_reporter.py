# Copyright 2018 Google Inc.  All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generates preprocess report.

The report is aimed to help the user to easily import the malformed/incompatible
VCF files. It contains three parts and each of them reports one type of error.
- The first part reports the header fields that have conflicting definitions
  across multiple VCF files, by providing the conflicting definitions, the
  corresponding file paths, and the suggested resolutions.
- The second part contains the undefined header fields and the inferred
  definitions.
- The last part contains the malformed records.
TODO(allieychen): Eventually, it also contains the resource estimation.

Output example (assuming opening in spreedsheet):
Header Conflicts
ID    Category    Conflicts            File Paths    Proposed Resolution
NS    INFO        num=1 type=Float     file1         num=1 type=Float
                  num=1 type=Integer   file2
DP    FORMAT      num=1 type=Float     file1         num=1 type=Float
                                       file2
                  num=1 type=Integer   file3

Undefined Headers
ID    Category    Proposed Resolution
GT    INFO        num=1 type=Float
GQ    FORMAT      num=1 type=Float

Malformed Records
File Path   Variant Record              Error Message
file 1      rs6 G A 29 PASS NS=3;       invalid literal for int() with base 10.
"""

from typing import Dict, List, Optional, Union  # pylint: disable=unused-import

from apache_beam.io import filesystems

from gcp_variant_transforms.beam_io import vcfio  # pylint: disable=unused-import
from gcp_variant_transforms.beam_io import vcf_header_io
from gcp_variant_transforms.beam_io.vcf_header_io import VcfParserHeaderKeyConstants
from gcp_variant_transforms.transforms import merge_header_definitions  # pylint: disable=unused-import
from gcp_variant_transforms.transforms.merge_header_definitions import Definition  # pylint: disable=unused-import


_NO_SOLUTION_MESSAGE = 'Not resolved.'
_PADDING_CHARACTER = ' '
_DELIMITER = '\t'


class _InconsistencyType(object):
  """Inconsistency types that included in the report."""
  HEADER_CONFLICTS = 'Header Conflicts'
  UNDEFINED_HEADERS = 'Undefined Headers'
  MALFORMED_RECORDS = 'Malformed Records'


class _HeaderLine(object):
  """Header lines for each error type."""
  CONFLICTS_HEADER = 'ID\tCategory\tConflicts\tFile Paths\tProposed Resolution'
  UNDEFINED_FIELD_HEADER = 'ID\tCategory\tProposed Resolution'
  MALFORMED_RECORDS_HEADER = 'File Path\tVariant Record\tError Message'


def generate_report(
    header_definitions,  # type: merge_header_definitions.VcfHeaderDefinitions
    file_path,  # type: str
    resolved_headers=None,  # type: vcf_header_io.VcfHeader
    inferred_headers=None,  # type: vcf_header_io.VcfHeader
    malformed_records=None  # type: List[vcfio.MalformedVcfRecord]
    ):
  # type: (...) -> None
  """Generates a report.

  Args:
    header_definitions: The container which contains all header definitions and
      the corresponding file names.
    file_path: The location where the report is saved.
    resolved_headers: The ``VcfHeader`` that provides the resolutions for the
      fields that have conflicting definitions.
    inferred_headers: The ``VcfHeader`` that contains the inferred header
      definitions of the undefined header fields.
    malformed_records: A list of ``MalformedVcfRecord`` for which VCF parser
      failed.
  """
  resolved_headers = resolved_headers or vcf_header_io.VcfHeader()
  with filesystems.FileSystems.create(file_path) as file_to_write:
    _append_conflicting_headers_to_report(file_to_write, header_definitions,
                                          resolved_headers)
    _append_inferred_headers_to_report(file_to_write, inferred_headers)
    _append_malformed_records_to_report(file_to_write, malformed_records)


def _extract_conflicts(
    definitions  # type: Dict[str, Dict[Definition, List[str]]]
    ):
  # type: (...) -> Dict[str, Dict[Definition, List[str]]]
  """Extracts the fields that have conflicting definitions.

  Returns:
    A dictionary that maps field id with conflicting definitions to a dictionary
    which maps ``Definition`` to a list of file names.
  """
  # len(v) > 1 means there are conflicting definitions for this field.
  return dict([(k, v) for k, v in definitions.items() if len(v) > 1])


def _append_conflicting_headers_to_report(
    file_to_write,  # type: file
    header_definitions,  # type: merge_header_definitions.VcfHeaderDefinitions
    resolved_headers  # type: vcf_header_io.VcfHeader
    ):
  # type: (...) -> None
  """Appends the human readable conflicting headers to the report.

  Output example:
  Header Conflicts
  ID    Category    Conflicts            File Paths    Proposed Resolution
  NS    INFO        num=1 type=Float     file1         num=1 type=Float
                    num=1 type=Integer   file2
  DP    FORMAT      num=1 type=Float     file1         num=1 type=Float
                                         file2
                    num=1 type=Integer   file3
  """
  content_lines = []
  content_lines.extend(_generate_conflicting_headers_lines(
      _extract_conflicts(header_definitions.infos), resolved_headers.infos,
      vcf_header_io.HeaderTypeConstants.INFO))
  content_lines.extend(_generate_conflicting_headers_lines(
      _extract_conflicts(header_definitions.formats), resolved_headers.formats,
      vcf_header_io.HeaderTypeConstants.FORMAT))
  _append_to_report(file_to_write, _InconsistencyType.HEADER_CONFLICTS,
                    _HeaderLine.CONFLICTS_HEADER, content_lines)


def _append_inferred_headers_to_report(file_to_write, inferred_headers):
  # type: (file, Optional[vcf_header_io.VcfHeader]) -> None
  """Appends the human readable inferred headers to the report.

  Output example:
  Undefined Headers
  ID    Category    Proposed Resolution
  GT    INFO        num=1 type=Float
  GQ    FORMAT      num=1 type=Float
  """
  if inferred_headers is not None:
    content_lines = []
    content_lines.extend(_generate_inferred_headers_lines(
        inferred_headers.infos, vcf_header_io.HeaderTypeConstants.INFO))
    content_lines.extend(_generate_inferred_headers_lines(
        inferred_headers.formats, vcf_header_io.HeaderTypeConstants.FORMAT))
    _append_to_report(file_to_write, _InconsistencyType.UNDEFINED_HEADERS,
                      _HeaderLine.UNDEFINED_FIELD_HEADER, content_lines)


def _append_malformed_records_to_report(file_to_write, malformed_records):
  # type: (file, Optional[List[MalformedVcfRecord]]) -> None
  """Appends the human readable malformed records to the report.

  Output example:
  Malformed Records
  File Path   Variant Record             Error Message
  file 1      rs6 G A 29 PASS NS=3;      invalid literal for int() with base 10.
  """
  if malformed_records is not None:
    content_lines = []
    for record in malformed_records:
      content_lines.append(_DELIMITER.join([record.file_name,
                                            record.line,
                                            record.error]))
    _append_to_report(file_to_write, _InconsistencyType.MALFORMED_RECORDS,
                      _HeaderLine.MALFORMED_RECORDS_HEADER, content_lines)


def _generate_conflicting_headers_lines(
    conflicts,  # type: Dict[str, Dict[Definition, List[str]]]
    resolved_headers,  # type: Dict[str, Dict[str, Union[str, int]]
    category  # type: str
    ):
  # type: (...) -> List[str]
  """Returns the conflicting headers lines for the report.

  Each conflicting header record is structured into columns(TAB separated
  values): the ID, the category('FOMRAT' or 'INFO'), conflicting definitions,
  file names and the resolution. To make the contents more readable (especially
  the file names can be extremely long and there can be at most 5 of them), the
  conflicting definitions and the file names are split in the continuous rows
  such that each row/cell only contains one definition/file name. While
  splitting, the empty cells are filled by ``_PADDING_CHARACTER`` as a
  placeholder so it can be easily viewed in both text editor and spreadsheets.
  Output example:
  NS    INFO        num=1 type=Float     file1         num=1 type=Float
                    num=1 type=Integer   file2
  """
  content_lines = []
  for field_id, definitions_to_files_map in conflicts.iteritems():
    first_item = True
    for definition, file_names in definitions_to_files_map.iteritems():
      if first_item:
        row = [field_id,
               category,
               _format_definition(definition.num, definition.type),
               file_names[0],
               _extract_resolution(resolved_headers, field_id)]
        first_item = False
      else:
        row = [_PADDING_CHARACTER,
               _PADDING_CHARACTER,
               _format_definition(definition.num, definition.type),
               file_names[0],
               _PADDING_CHARACTER]
      content_lines.append(_DELIMITER.join(row))
      for file_name in file_names[1:]:
        row = [_PADDING_CHARACTER,
               _PADDING_CHARACTER,
               _PADDING_CHARACTER,
               file_name,
               _PADDING_CHARACTER]
        content_lines.append(_DELIMITER.join(row))
  return content_lines


def _generate_inferred_headers_lines(inferred_headers, category):
  # type: (Dict[str, Dict[str, Union[str, int]]], str) -> List[str]
  """Returns the inferred headers lines for the report.

  The field ID, category (FORMAT or INFO), and the inferred header definitions
  are included in the contents.
  Output example:
  GT    INFO    num=1 type=Float
  """
  content_lines = []
  for field_id in inferred_headers.keys():
    row = [field_id,
           category,
           _extract_resolution(inferred_headers, field_id)]
    content_lines.append(_DELIMITER.join(row))
  return content_lines


def _extract_resolution(header, filed_id):
  # type: (Dict[str, Dict[str, Union[str, int]]], str) -> str
  """Extracts the resolution.

  Output example:
  num=1 type=Float
  """
  if filed_id not in header:  # It happens when no resolved_headers is provided.
    return _NO_SOLUTION_MESSAGE
  return _format_definition(header[filed_id][VcfParserHeaderKeyConstants.NUM],
                            header[filed_id][VcfParserHeaderKeyConstants.TYPE])


def _format_definition(num_value, type_value):
  # type: (Union[str, int], str) -> str
  formatted_definition = [
      '='.join([VcfParserHeaderKeyConstants.NUM, str(num_value)]),
      '='.join([VcfParserHeaderKeyConstants.TYPE, str(type_value)])
  ]
  return ' '.join(formatted_definition)


def _append_to_report(file_to_write, error_type, header, contents):
  # type: (file, str, str, List[str]) -> None
  """Appends the contents to ``file_to_write``.

  The ``error_type``, ``header`` and the ``contents`` are written to
  ``file_to_write`` sequentially.
  """
  if not contents:
    file_to_write.write('No ' + error_type + ' Found.\n')
  else:
    file_to_write.write(error_type + '\n')
    file_to_write.write(header + '\n')
    for content in contents:
      file_to_write.write(content + '\n')
  file_to_write.write('\n')
