# SPDX-FileCopyrightText: 2020 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
#
# SPDX-License-Identifier: Apache-2.0

import re
import itertools
import logging


class Function:
    def __init__(self, name, args=None, indirect=False, source_file="", line_numbers=None):
        self.name = name.strip()
        self.args = args or []
        self.indirect = indirect
        self.source_file = source_file.strip()
        self.line_numbers = line_numbers or []

    '''For purposes of call graph, arguments are ignored in equivalence check'''
    def __eq__(self, other):
        return self.name == other.name

    def __ne__(self, other):
        return self.name != other.name

    def __lt__(self, other):
        return (self.name < other.name)

    def ansi_highlight(self, text):
        return "\033[38;5;110m%s\033[0m" % text

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        if not self.args:
            return self.name
        else:
            return '%s(%s)' % (self.name, self.ansi_highlight(', '.join(self.args)))

    def equal(self, other):
        # this is different from the comparison using built in comparison mechanism (==)
        return self.name == other.name and \
            self.args == other.args and \
            self.indirect == other.indirect and \
            self.source_file == other.source_file and \
            self.line_numbers == other.line_numbers


def find_ops_structures(llvm_file, indirect_func_names):
    structs_and_member_names = {}  # contains only subset of those that match structs_and_member_indices
    structs_and_member_indices = {}
    if indirect_func_names is None:
        return structs_and_member_indices, structs_and_member_names

    re_structure_type = re.compile(
        r'DW_TAG_structure_type, name: "(?P<struct>\w+)".*elements: !(?P<elements_label>\d+)')
    re_elements = re.compile(r'!(?P<label>\d+) = !{(?P<elements>.*)}')
    re_member = re.compile(r'!(?P<label>\d+) = .*DW_TAG_member, name: "(?P<member>\w+)"')

    for operation in indirect_func_names:
        struct_name, op_name = operation.split('.')
        elements_label = None
        elements = None

        llvm_file.seek(0)

        for line in llvm_file:
            m = re_structure_type.search(line)
            if m and m.group('struct') == struct_name:
                elements_label = m.group('elements_label')
                break

        for line in llvm_file:
            m = re_elements.search(line)
            if m and m.group('label') == elements_label:
                elements = re.findall(r'\d+', m.group('elements'))
                break

        for line in llvm_file:
            m = re_member.search(line)
            if m and m.group('label') in elements and m.group('member') == op_name:
                op_index = elements.index(m.group('label'))
                if struct_name not in structs_and_member_names:
                    structs_and_member_names[struct_name] = []
                    structs_and_member_indices[struct_name] = []
                structs_and_member_names[struct_name].append(op_name)
                structs_and_member_indices[struct_name].append(op_index)
                break

    llvm_file.seek(0)
    # print('find_ops_structures: %s: ' + str(structs_and_member_indices)) % llvm_file.name
    return structs_and_member_indices, structs_and_member_names


def get_debug_entries(llvm_file):
    llvm_file.seek(0)

    # !20 = !DIFile(filename: "/home/hogander/work/DustRacing2D/src/editor/editorview.cpp", directory:
    #     "/home/hogander/work/DustRacing2D")
    re_debug_entry_file = re.compile(
        r'^(?P<key>\![0-9]+) = !DIFile\(filename\: \"(?P<filename>.*)\", directory\: \"(?P<directory>.*)\"\)')

    # !1764 = !DILocation(line: 26, column: 10, scope: !1758)
    re_debug_entry_location = re.compile(
        r'^(?P<key>\![0-9]+) = !DILocation\(line\: (?P<line>[0-9]+).*scope\: (?P<scope>\![0-9]+)')

    # !2261 = distinct !DISubprogram(name: "update", linkageName: "_ZN2AI6updateEb", scope: !1662, file: !26, \
    #     line: 42, type: !2214, isLocal: false, isDefinition: true, scopeLine: 43, flags: DIFlagPrototyped, \
    #     isOptimized: false, unit: !25, declaration: !2213, variables: !160)
    re_debug_entry_subprogram = re.compile(
        r'(?P<key>\![0-9]+) = distinct !DISubprogram.*\(.*file\: (?P<file>\![0-9]+).*line\: (?P<line>[0-9]+)')

    # !2497 = distinct !DISubprogram(linkageName: "_GLOBAL__sub_I_delta_logger.cpp", scope: !20, file: !20,
    #     type: !2498, isLocal: true, isDefinition: true, flags: DIFlagArtificial, isOptimized: false, unit: \
    #     !19, variables: !957)
    re_debug_entry_subprogram_wo_line = re.compile(
        r'(?P<key>\![0-9]+) = distinct !DISubprogram.*\(linkageName.*file\: (?P<file>\![0-9]+)')

    # !6769 = distinct !DILexicalBlock(scope: !6764, file: !477, line: 175, column: 9)
    # !6589 = !DILexicalBlockFile(scope: !6355, file: !6357, discriminator: 0)
    re_debug_entry_lexical = re.compile(r'^(?P<key>\![0-9]+) = .*!DILexicalBlock.*\(.*file\: (?P<file>\![0-9]+)')

    debug_cache_entries = {}

    # Read all the debug entries we are interested
    for line in llvm_file:
        # Simple file. No further parsing needed
        m = re_debug_entry_file.search(line)
        if m:
            logging.debug('Adding simple file debug entry key = ' + m.group('key') + ', filename = ' +
                          m.group('filename'))
            debug_cache_entries[m.group('key')] = {'filename': m.group('filename')}
            continue

        # Location, scope and filename are parsed later
        m = re_debug_entry_location.search(line)
        if m:
            logging.debug('Adding debug cache entry key = ' + m.group('key') + ', line = ' + m.group('line'))
            debug_cache_entries[m.group('key')] = {'line': m.group('line'), 'scope': m.group('scope')}
            continue

        # Subprogram, filename is parsed later
        m = re_debug_entry_subprogram.search(line)
        if m:
            logging.debug('Adding debug cache entry key = ' + m.group('key') + ', file = ' + m.group('file') +
                          ', linenumber = ' + m.group('line'))
            debug_cache_entries[m.group('key')] = {'line': m.group('line'), 'file': m.group('file')}
            continue

        # Subprogram without linenumber, filename is parsed later
        m = re_debug_entry_subprogram_wo_line.search(line)
        if m:
            logging.debug('Adding debug cache entry key = ' + m.group('key') + ', file = ' + m.group('file'))
            debug_cache_entries[m.group('key')] = {'file': m.group('file')}
            continue

        # Lexical block, filename is parsed later
        m = re_debug_entry_lexical.search(line)
        if m:
            logging.debug('Adding debug cache entry key = ' + m.group('key') + ', file = ' + m.group('file'))
            debug_cache_entries[m.group('key')] = {'file': m.group('file')}

    debug_entries = {}

    for key in debug_cache_entries.keys():
        # Lexical/or file, no need to parse it further or add it to debug_entries.
        if 'line' not in debug_cache_entries[key]:
            continue

        if 'scope' in debug_cache_entries[key]:
            # Scope needs parsing one more level
            filename = debug_cache_entries[debug_cache_entries[debug_cache_entries[key]['scope']]['file']]['filename']
        else:
            filename = debug_cache_entries[debug_cache_entries[key]['file']]['filename']

        line = debug_cache_entries[key]['line']

        logging.debug('Adding debug entry key = ' + key + ', filename = ' + filename + ', line = ' + line)
        debug_entries[key] = {'line': line, 'filename': filename}

    llvm_file.seek(0)

    return debug_entries


def discard_duplicate_callees(callees_in_list, callees_to_be_added):
    uniq_list = []
    for callee_tba in callees_to_be_added:
        uniq = True
        for callee in callees_in_list:
            if callee.name == callee_tba.name and callee.source_file == callee_tba.source_file and \
               callee.line_numbers == callee_tba.line_numbers:
                uniq = False
                break
        if uniq:
            uniq_list += [callee_tba]
        else:
            logging.debug('Discarding duplicate callee ' + callee_tba.source_file + ':' + callee_tba.name + ':' +
                          str(callee_tba.line_numbers))
    return uniq_list


def llvm_source_name(llvm_file):
    source_name = 'Unknown'

    llvm_file.seek(0)
    # source_filename = "/some/path/to/file.x"\n
    # source_filename = '/some/path/to/file.x"\n
    re_source = re.compile(r'source_filename\s*=\s*["\'](?P<sourcename>[^"\']+)["\']')
    for line in llvm_file:
        m = re_source.search(line)
        if m:
            source_name = m.group('sourcename')
            break

    llvm_file.seek(0)
    return source_name


def parse_llvm(llvm_file, call_graph, call_graph_lock, indirect_nodes=None):
    # define void @_ZN7ObjectsC2Ev(%class.Objects*) unnamed_addr #0 align 2 !dbg !1703 {
    re_define = re.compile(r'define.+?@(?P<name>\w+)\((?P<args>.*)\).*!dbg (?P<debug_entry>\![0-9]+) {')
    # call void @_ZSt8_DestroyISt10shared_ptrI10ObjectBaseEEvPT_(%"class.std::shared_ptr"* %11) #11, !dbg !2932
    re_call = re.compile(r'call.+?@(?!llvm)(?P<name>[a-zA-Z0-9_\.:]*)\((?P<args>.*)\).*!dbg (?P<debug_entry>\![0-9]+)')
    # invoke void @__cxa_rethrow() #13
    re_invoke = re.compile(r'invoke.+?@(?!llvm)(?P<name>[a-zA-Z0-9_\.:]*)\((?P<args>.*)\)')
    re_funcptr = re.compile(r'@(?P<funcptr>\w+)')
    re_getelementptr = re.compile(
        r'%(?P<label>\w+) = getelementptr inbounds %struct.(?P<structure>\w+),.*i32 0, i32 (?P<index>[0123456789]+)')
    re_load = re.compile(r'%(?P<label_out>\w+) = load .*%(?P<label_in>[0123456789]+), ')
    re_store = re.compile(r'store.+?%(?P<label_src>[0123456789]+), .*%(?P<label_dst>[0123456789]+), ')
    re_indirect_call = re.compile(r'call.+?%(?P<label>\w+)\((?P<args>.*)\).*!dbg (?P<debug_entry>\![0-9]+)')

    structs, names = find_ops_structures(llvm_file, indirect_nodes)
    indirect_labels = []

    debug_entries = get_debug_entries(llvm_file)
    source_file = llvm_source_name(llvm_file)

    caller = None
    callees = []
    for line in llvm_file:
        invoke = False
        m = re_define.search(line)
        if m:
            if caller:
                with call_graph_lock:
                    call_graph[caller] += discard_duplicate_callees(call_graph[caller], callees)

            debug_entry = m.group('debug_entry')
            line_number = debug_entries[debug_entry]['line']
            filename = 'filename' in debug_entries[debug_entry] and debug_entries[
                debug_entry]['filename'] or source_file
            caller = Function(m.group('name'), source_file=filename, line_numbers=[line_number])

            callees = []

            indirect_labels = []

            with call_graph_lock:
                if caller not in call_graph:
                    call_graph[caller] = []

            logging.debug('Caller ' + caller.name + ' defined in ' + filename + ' at line ' + line_number)
        else:
            m = re_call.search(line)
            if not m:
                invoke = True
                m = re_invoke.search(line)
            if m:
                callee_name = m.group('name')
                if callee_name.startswith('llvm'):
                    continue
                callee_args = []
                if invoke:
                    # to label %20 unwind label %27, !dbg !2764
                    line = next(llvm_file)
                    debug_entry = line.split('!dbg')[1].strip()
                else:
                    debug_entry = m.group('debug_entry')
                m = re_funcptr.findall(m.group('args'))
                if m:
                    callee_args = m

                line_number = debug_entries[debug_entry]['line']
                filename = 'filename' in debug_entries[debug_entry] and debug_entries[debug_entry][
                    'filename'] or source_file

                callee = Function(callee_name, callee_args, source_file=filename)

                if callee not in callees:
                    callees += [callee]
                else:
                    callee = callees[callees.index(callee)]

                callee.line_numbers += [line_number]
                logging.debug('Call to ' + callee.name + ' from ' + caller.name + ' in file ' + filename +
                              ' at line ' + line_number)
            else:
                m = re_getelementptr.search(line)
                if m:
                    if m.group('structure') in structs and int(m.group('index')) in structs[m.group('structure')]:
                        struct_index = structs[m.group('structure')].index(int(m.group('index')))
                        struct_struct_index = names[m.group('structure')][struct_index]
                        current_operation = m.group('structure') + '.' + struct_struct_index
                        indirect_labels = []  # reset old labels at each getelementptr
                        indirect_labels.append(m.group('label'))
                else:
                    m = re_load.search(line)
                    if m:
                        if m.group('label_in') in indirect_labels:
                            indirect_labels.append(m.group('label_out'))
                    else:
                        m = re_store.search(line)
                        if m:
                            if m.group('label_src') in indirect_labels:
                                indirect_labels.append(m.group('label_dst'))
                        else:
                            m = re_indirect_call.search(line)
                            if m:
                                if m.group('label') in indirect_labels:
                                    debug_entry = m.group('debug_entry')
                                    line_number = debug_entries[debug_entry]['line']
                                    filename = 'filename' in debug_entries[debug_entry] and debug_entries[
                                        debug_entry]['filename'] or source_file
                                    callee = Function(current_operation, source_file=filename)
                                    if callee not in callees:
                                        callees += [callee]
                                    else:
                                        callee = callees[callees.index(callee)]

                                    callee.line_numbers += [line_number]
                                    logging.debug('Call to ' + callee.name + ' from ' + caller.name + ' in file ' +
                                                  filename + ' at line ' + line_number)
    if caller:
        with call_graph_lock:
            call_graph[caller] += discard_duplicate_callees(call_graph[caller], callees)