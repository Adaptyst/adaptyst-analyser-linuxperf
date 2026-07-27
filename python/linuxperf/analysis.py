# SPDX-FileCopyrightText: 2026 CERN
# SPDX-License-Identifier: GPL-3.0-or-later

import re
import json
import csv
import sys
from zipfile import ZipFile
from zipfile import Path as ZipFilePath
from treelib import Tree
from pathlib import Path
from collections import deque, defaultdict
from adaptystanalyser import \
    Module, Identifier, Session, Window, Analysable


class TimelineWindow(Window):
    def __init__(self, session: Session, module: Module):
        """
        Construct a TimelineWindow object.

        :param Session session: Instance of a performance analysis
                                session.
        :param Module module: Instance of a module the timeline
                              window belongs to (this is always
                              a LinuxperfModule object).
        """
        self._session = session
        self._module = module

    def get_module(self) -> Module:
        return self._module

    def get_type(self) -> str:
        return 'linuxperf_timeline'

    def get_constr_args(self) -> list:
        return []

    def get_dependencies(self) -> list:
        return []

    def get_data(self):
        return None

    def get_session(self) -> Session:
        return self._session

    def get_analysable(self) -> Analysable:
        return self._module.get_analysable()

    def get_init_data(self):
        return None


class FlameGraphWindow(Window):
    def __init__(self, timeline_window: TimelineWindow,
                 pid: int, tid: int):
        """
        Construct a FlameGraphWindow object.

        :param TimelineWindow timeline_window: Instance of a timeline
                                               window (the web side
                                               uses this for getting
                                               data necessary for displaying
                                               flame graphs).
        :param int pid: PID of a thread/process for which flame graphs
                        should be displayed.
        :param int tid: TID of a thread/process for which flame graphs
                        should be displayed.
        """
        self._timeline_window = timeline_window
        self._pid = pid
        self._tid = tid

    def get_module(self) -> Module:
        return self._timeline_window.get_module()

    def get_type(self) -> str:
        return 'linuxperf_flame_graph'

    def get_constr_args(self) -> list:
        return []

    def get_dependencies(self) -> list:
        return [self._timeline_window]

    def get_data(self):
        return None

    def get_init_data(self):
        return {
            'timeline_group_id': f'{self._pid}_{self._tid}'
        }

    def get_analysable(self) -> Analysable:
        return self.get_module().get_analysable()

    def get_session(self) -> Session:
        return self._timeline_window.get_session()


class LinuxperfModule(Module):
    def __init__(self, session_id: Identifier, entity: str,
                 node: str):
        """
        Construct a LinuxperfModule object.

        :param Identifier session_id: Performance analysis
                                      session information in form
                                      of an Identifier object.
        :param str entity: Name of the entity of a node where
                           the module is attached.
        :param str node: Name of the node where the module is
                         attached.
        """
        self._entity_path = session_id.get_detailed_path(entity)
        self._path = session_id.get_detailed_path(entity, node,
                                                  self.get_name())

        self._threads_metadata = None

        self._metrics = {}
        self._roofline_info = {}
        self._thread_tree = None

        self._general_metrics = {}
        self._sources = {}
        self._source_index = {}
        self._source_zip_path = None

    def _load(self):
        with (self._path / 'threads.json').open(mode='r') as f:
            self._threads_metadata = json.load(f)

        with (self._path / 'dirmeta.json').open(mode='r') as f:
            self._metadata = json.load(f)

        for metric in filter(Path.is_dir, self._path.glob('*')):
            metric_path = metric / 'dirmeta.json'

            if not metric_path.exists():
                continue

            with metric_path.open(mode='r') as f:
                data = json.load(f)
                self._metrics[metric.name] = data
                self._metrics[metric.name]['flame_graph'] = True

                if len(self._roofline_info) == 0:
                    carm_match = re.search(r'^CARM_(\S+)_(\S+)$', data['title'])
                    if carm_match is not None:
                        cpu_type = carm_match.group(1)

                        if cpu_type == 'INTEL':
                            self._roofline_info = {
                                'cpu_type': 'Intel_x86',
                                'ai_keys': [
                                    'mem_inst_retired.any'
                                ],
                                'instr_keys': [
                                    'fp_arith_inst_retired.scalar_single',
                                    'fp_arith_inst_retired.scalar_double',
                                    'fp_arith_inst_retired.128b_packed_single',
                                    'fp_arith_inst_retired.128b_packed_double',
                                    'fp_arith_inst_retired.256b_packed_single',
                                    'fp_arith_inst_retired.256b_packed_double',
                                    'fp_arith_inst_retired.512b_packed_single',
                                    'fp_arith_inst_retired.512b_packed_double'
                                ]
                            }
                        elif cpu_type == 'AMD':
                            self._roofline_info = {
                                'cpu_type': 'AMD_x86',
                                'ai_keys': [
                                    'ls_dispatch:ld_dispatch',
                                    'ls_dispatch:store_dispatch'
                                ],
                                'instr_keys': [
                                    'retired_sse_avx_operations:sp_mult_add_flops',
                                    'retired_sse_avx_operations:dp_mult_add_flops',
                                    'retired_sse_avx_operations:sp_add_sub_flops',
                                    'retired_sse_avx_operations:dp_add_sub_flops',
                                    'retired_sse_avx_operations:sp_mult_flops',
                                    'retired_sse_avx_operations:dp_mult_flops',
                                    'retired_sse_avx_operations:sp_div_flops',
                                    'retired_sse_avx_operations:dp_div_flops'
                                ]
                            }

        if (self._path / 'roofline.csv').exists():
            self._general_metrics['roofline'] = {
                'title': 'Cache-aware roofline model'
            }

        if (self._path / 'sources.json').exists():
            with (self._path / 'sources.json').open(
                    mode='r') as f:
                self._sources = json.load(f)

        if (self._entity_path / 'src.zip').exists():
            self._source_zip_path = self._entity_path / 'src.zip'

        if self._source_zip_path is not None:
            src_index_path = self._path / 'src_index.json'

            if src_index_path.exists():
                with src_index_path.open(mode='r') as f:
                    self._source_index = json.load(f)
            else:
                with ZipFile(self._source_zip_path) as zip:
                    path = ZipFilePath(zip, 'index.json')

                    if path.exists():
                        with path.open(mode='r') as f:
                            index_str = f.read()

                        self._source_index = json.loads(index_str)

                        # TODO: Unpack this *outside* of the session
                        #
                        # with src_index_path.open(mode='w') as f:
                        #     f.write(index_str)

    def get_name(self):
        return 'linuxperf'

    @Module.needs_loading
    def get_general_analysis(self, analysis_type):
        """
        Get general analysis data of a specified type. If the type
        does not exist or the corresponding data could not be
        read, None is returned.

        Currently-supported general analysis types:
        * "roofline": cache-aware roofline benchmark analysis of
          a machine produced by the CARM Tool by INESC-ID
          (https://github.com/champ-hub/carm-roofline).
          The return value is a dictionary with the following structure:
          {
            "type": "roofline",
            "l1": <L1 cache size in bytes>,
            "l2": <L2 cache size in bytes>,
            "l3": <L3 cache size in bytes>,
            "models": <array of roofline models>
          }

          Each element of the array of roofline models has the following
          structure (for all references to --<option>, go to
          https://github.com/champ-hub/carm-roofline#how-to-use-cli):
          {
            "isa": "<instruction set architecture: see --isa for the
                     possible values>",
            "precision": "<floating-point precision: see --precision
                           for the format>",
            "threads": "<number of threads>",
            "loads": "<number of loads>",
            "stores": "<number of stores>",
            "interleaved": "<whether cores belong to interleaved NUMA domains:
                             the value is either Yes or No, see --interleaved
                             for more details>",
            "dram_bytes": "<number of DRAM bytes>",
            "fp_inst": "<floating-point instruction used: see --inst for
                         the format>",
            "l1": {
              "gbps": "<L1 performance in GB/s>",
              "instpc"; "<L1 instructions per cycle>"
            },
            "l2": {
              "gbps": "<L2 performance in GB/s>",
              "instpc": "<L2 instructions per cycle>"
            },
            "l3": {
              "gbps": "<L3 performance in GB/s>",
              "instpc": "<L3 instructions per cycle>"
            },
            "dram": {
              "gbps": "<DRAM performance in GB/s>",
              "instpc": "<DRAM instructions per cycle>"
            },
            "fp": {
              "gflops": "<floating-point performance in GFLOPS>",
              "instpc": "<floating-point instructions per cycle>"
            },
            "fp_fma": {
              "gflops": "<floating-point FMA performance in GFLOPS>",
              "instpc": "<floating-point FMA instructions per cycle>"
            }
        }

        :param str analysis_type: Type of a general analysis which
                                  data should be returned for.
        """
        if analysis_type == 'roofline':
            p = self._path / 'roofline.csv'

            if not p.exists():
                return None

            data = {
                'type': analysis_type,
                'l1': None,
                'l2': None,
                'l3': None,
                'models': []
            }

            with p.open(mode='r', newline='') as f:
                reader = csv.reader(f)

                first_header = next(reader)

                if len(first_header) != 21 or \
                   [first_header[0], first_header[2],
                    first_header[4], first_header[6]] + \
                    first_header[9:] != \
                    ['Name:', 'L1 Size:', 'L2 Size:',
                     'L3 Size:', 'L1', 'L1', 'L2', 'L2',
                     'L3', 'L3', 'DRAM', 'DRAM',
                     'FP', 'FP', 'FP FMA', 'FP_FMA']:
                    return None

                second_header = next(reader)

                if second_header != \
                    ['Date', 'ISA', 'Precision', 'Threads',
                     'Loads', 'Stores', 'Interleaved', 'DRAM Bytes',
                     'FP Inst.', 'GB/s', 'I/Cycle', 'GB/s',
                     'I/Cycle', 'GB/s', 'I/Cycle', 'GB/s',
                     'I/Cycle', 'Gflop/s', 'I/Cycle', 'Gflop/s',
                     'I/Cycle']:
                    return None

                data['l1'] = int(first_header[3])
                data['l2'] = int(first_header[5])
                data['l3'] = int(first_header[7])

                for row in reader:
                    if row is None or len(row) != 21:
                        continue

                    data['models'].append({
                        'isa': row[1],
                        'precision': row[2],
                        'threads': row[3],
                        'loads': row[4],
                        'stores': row[5],
                        'interleaved': row[6],
                        'dram_bytes': row[7],
                        'fp_inst': row[8],
                        'l1': {
                            'gbps': row[9],
                            'instpc': row[10]
                        },
                        'l2': {
                            'gbps': row[11],
                            'instpc': row[12]
                        },
                        'l3': {
                            'gbps': row[13],
                            'instpc': row[14]
                        },
                        'dram': {
                            'gbps': row[15],
                            'instpc': row[16]
                        },
                        'fp': {
                            'gflops': row[17],
                            'instpc': row[18]
                        },
                        'fp_fma': {
                            'gflops': row[19],
                            'instpc': row[20]
                        }
                    })

            return data
        else:
            return None

    def get_timeline_window(self):
        """
        Get a TimelineWindow object (an adaptystanalyser.Window subclass)
        corresponding to the timeline view of all threads/processes
        captured during a linuxperf performance analysis session. This
        can be used e.g. to save a single window arrangement with the
        timeline and share it with others programmatically
        (use Window.save_arrgmt() and Window.get_arrgmt_url() for this).
        """
        return TimelineWindow(self.get_session(), self)

    @Module.needs_loading
    def get_flame_graph_window(self, pid: int, tid: int):
        """
        Get a FlameGraphWindow object (an adaptystanalyser.Window subclass)
        corresponding to a flame graph of a thread/process with the given
        PID and TID. This can be used e.g. to save a single window
        arrangement with a given flame graph and share it with others
        programmatically (use Window.save_arrgmt() and Window.get_arrgmt_url()
        for this).

        If no valid value of PID and/or TID is provided, None is returned.

        :param int pid: PID of a thread/process.
        :param int tid: TID of a thread/process.
        """
        tree = self.get_thread_tree()
        node = tree.get_node(str(tid))

        if node is None:
            return None

        if node.tag[1] != f'{pid}/{tid}':
            return None

        return FlameGraphWindow(TimelineWindow(self.get_session(), self),
                                pid, tid)

    @Module.needs_loading
    def get_flame_graph(self, pid, tid, compress_threshold, region=None):
        """
        Get a flame graph of the thread/process with a given PID and TID
        to be rendered by d3-flame-graph, taking into account to collapse
        blocks taking less than a specified share of total samples.

        :param int pid: PID of a thread/process.
        :param int tid: TID of a thread/process.
        :param float compress_threshold: A compression threshold. For
                                         example, if its value is 0.10,
                                         blocks taking less than 10% of
                                         total samples will be collapsed.
        """
        data = {}

        for p in self._path.glob(f'*/{pid}/{tid}'):
            data[p.parent.parent.name] = []

        # Untimed
        for metric in data.keys():
            start_path = self._path / metric / str(pid) / str(tid) / \
                ('untimed.json' if region is None else f'{region}_untimed.json')
            with start_path.open(mode='r') as f:
                data[metric].append(json.load(f))

        # Timed
        for metric in data.keys():
            start_path = self._path / metric / str(pid) / str(tid) / \
                ('timed.json' if region is None else f'{region}_timed.json')
            with start_path.open(mode='r') as f:
                data[metric].append(json.load(f))

        # Processing
        for k, v in data.items():
            if len(v) != 2:
                raise RuntimeError(f'{k} in {pid}_{tid}.json should have '
                                   f'exactly 2 elements, but it has {len(v)}')

            compressed_blocks_lists = [[], []]
            queue = deque([(v[0], v[0]['value'], False, False,
                            compressed_blocks_lists[0]),
                           (v[1], v[1]['value'], True, False,
                            compressed_blocks_lists[1])])

            while len(queue) > 0:
                result, total, time_ordered, parent_is_compressed, \
                    compressed_blocks = queue.pop()

                children = result['children']
                new_children = []
                compressed_value = 0
                hidden_children = []
                compressed_children = set()

                for i, child in enumerate(children):
                    if child['value'] < compress_threshold * total:
                        compressed_children.add(i)
                    else:
                        queue.append((child, total, time_ordered, False,
                                      compressed_blocks))

                for i, child in enumerate(children):
                    if time_ordered:
                        if i in compressed_children:
                            compressed_value += child['value']
                            hidden_children.append(child)
                        else:
                            if compressed_value > 0:
                                if compressed_value == total \
                                   and parent_is_compressed:
                                    new_children += hidden_children
                                else:
                                    new_child = {
                                        'name': '(compressed)',
                                        'value': compressed_value,
                                        'children': hidden_children,
                                        'compressed_id': len(compressed_blocks)
                                    }

                                    queue.append((new_child,
                                                  compressed_value,
                                                  time_ordered,
                                                  True,
                                                  compressed_blocks))

                                    compressed_blocks.append(new_child)
                                    new_children.append(new_child)

                                compressed_value = 0
                                hidden_children = []

                            new_children.append(child)
                    else:
                        if i in compressed_children:
                            compressed_value += child['value']
                            hidden_children.append(child)
                        else:
                            new_children.append(child)

                if compressed_value > 0:
                    if len(hidden_children) == 1 and \
                       len(hidden_children[0]['children']) == 0:
                        new_children += hidden_children
                    elif compressed_value == total and parent_is_compressed:
                        if len(hidden_children) > 1:
                            part1_cnt = len(hidden_children) // 2

                            compressed_value_part1 = 0
                            for i in range(part1_cnt):
                                compressed_value_part1 += \
                                    hidden_children[i]['value']

                            compressed_value_part2 = compressed_value - \
                                compressed_value_part1

                            new_child1 = {
                                'name': '(compressed)',
                                'value': compressed_value_part1,
                                'children': hidden_children[:part1_cnt],
                                'compressed_id': len(compressed_blocks)
                            }

                            new_child2 = {
                                'name': '(compressed)',
                                'value': compressed_value_part2,
                                'children': hidden_children[part1_cnt:],
                                'compressed_id': len(compressed_blocks) + 1
                            }

                            queue.append((new_child1, compressed_value_part1,
                                          time_ordered, True,
                                          compressed_blocks))
                            queue.append((new_child2, compressed_value_part2,
                                          time_ordered, True,
                                          compressed_blocks))

                            compressed_blocks.append(new_child1)
                            compressed_blocks.append(new_child2)

                            new_children.append(new_child1)
                            new_children.append(new_child2)
                        else:
                            new_children += hidden_children
                    else:
                        new_child = {
                            'name': '(compressed)',
                            'value': compressed_value,
                            'children': hidden_children,
                            'compressed_id': len(compressed_blocks)
                        }

                        queue.append((new_child, compressed_value,
                                      time_ordered, True,
                                      compressed_blocks))

                        compressed_blocks.append(new_child)
                        new_children.append(new_child)

                if 'compressed_id' in result:
                    result['children'] = []
                    result['hidden_children'] = new_children
                else:
                    result['children'] = new_children

            for compressed_blocks in compressed_blocks_lists:
                deleted_block_ids = set()
                for block in compressed_blocks:
                    if block['compressed_id'] in deleted_block_ids:
                        continue

                    while (len(block['hidden_children']) == 1 and
                           'hidden_children' in block['hidden_children'][0]):
                        deleted_block_ids.add(
                            block['hidden_children'][0]['compressed_id'])
                        block['hidden_children'] = \
                            block['hidden_children'][0]['hidden_children']

        return json.dumps(data)

    @Module.needs_loading
    def get_callchain_mappings(self, event_type=None):
        """
        Get a dictionary mapping compressed callchain names of a given
        event type to a two-element array [<full symbol name>,
        <library/executable name>].

        If event_type is None (by default), a wrapper dictionary is
        returned for all available event types with structure
        {
          "<event type>": <result of get_callchain_mappings("<event type>")>
        }

        If event_type is "syscall", a dictionary for compressed
        callchain names captured during thread/process tree profiling
        is returned.

        If event_type is invalid or does not exist, None is returned.
        """

        if event_type is None:
            result_dict = {}

            if (self._path / 'callchains.json').exists():
                with (self._path / 'callchains.json').open(mode='r') as f:
                    result_dict['syscall'] = json.load(f)

            for k in self._metrics.keys():
                path = self._path / k / 'callchains.json'

                if not path.exists():
                    continue

                with path.open(mode='r') as f:
                    result_dict[k] = json.load(f)

            return result_dict
        elif event_type == 'syscall':
            if (self._path / 'callchains.json').exists():
                with (self._path / 'callchains.json').open(mode='r') as f:
                    return json.load(f)

            return {}
        else:
            if event_type in self._metrics.keys():
                path = self._path / event_type / 'callchains.json'

                if not path.exists():
                    return None

                with path.open(mode='r') as f:
                    return json.load(f)

            return None

    @Module.needs_loading
    def get_thread_tree(self) -> Tree:
        """
        Get a treelib.Tree object representing the thread/process tree.

        Each node corresponds to a thread/process: its identifier is equal
        to the TID and its tag is in form of ["<thread/process name>",
        "<PID>/<TID>", <exact start time in ns>, <exact runtime in ns>].
        """
        if self._thread_tree is not None:
            return self._thread_tree

        tree = Tree()

        for n in self._threads_metadata['tree']:
            tree.create_node(**n)

        self._thread_tree = tree
        return tree

    @Module.needs_loading
    def get_json_tree(self):
        """
        Get a JSON object string representing the thread/process tree.

        The returned object is the root, which describes the very first
        process detected along with its children.
        The object has the following keys:
        * "id": the unique identifier of a thread/process in form of
          "<PID>_<TID>".
        * "start_time": the timestamp of the moment when the thread/process
           was effectively started, in milliseconds.
        * "runtime": the number of milliseconds the thread/process was
          running for.
        * "sampled_time": the number of milliseconds the thread/process
          was running for, as sampled by "perf".
        * "name": the process name.
        * "pid_tid": the PID and TID pair string in form of "<PID>/<TID>".
        * "off_cpu": the list of intervals when the thread/process was
          off-CPU. Each interval is in form of (a, b), where a is the
          start time of an off-CPU interval and b is the length of such
          interval.
        * "start_callchain": the callchain spawning the thread/process.
        * "metrics": the JSON object mapping extra per-thread profiling metrics
          (in addition to on-CPU/off-CPU activity) to their website titles
          and their type (i.e. flame-graph-related or not flame-graph-related).
          An example object is {"page-faults": {"title": "Page faults",
          "flame_graph": true}}. The structure can also be empty.
        * "general_metrics": the JSON object mapping general profiling
          metrics to their website titles and other auxiliary data (e.g.
          {"roofline": {"title": "Roofline model", ...}). This is set
          only for the root and it can be empty.
        * "src": the return value of get_sources(), see its documentation
          for the details. This is set only for the root.
        * "src_index": the return value of get_source_index(), see its
          documentation for the details. This is set only for the root.
        * "children": the list of all threads/processes spawned by the
          thread/process. Each element has the same structure as the root
          except for elements indicated as "set only for the root".
        * "roofline": the JSON object with information necessary for
          interpreting roofline profiling results. The structure is as follows:
          {"cpu_type": "<CPU type, e.g. Intel_x86>", "ai_keys": [<events for
          calculating arithmetic intensity>], "instr_keys": [<events for
          calculating FLOPS etc.>]}. This is set only for the root and
          it can be empty.
        """
        def to_ms(num):
            return None if num is None else num / 1000000

        tree = self.get_thread_tree()

        def node_to_dict(node, is_root):
            process_name, pid_tid, start_time, runtime = node.tag
            pid, tid = pid_tid.split('/')

            start_time = to_ms(start_time)
            if runtime != -1:
                runtime = to_ms(runtime)

            offcpu_path = self._path / 'walltime' / pid / tid / 'offcpu.dat'
            offcpu_regions = []

            if offcpu_path.exists():
                with offcpu_path.open(mode='r') as f:
                    for line in f:
                        line = line.strip()

                        if len(line) == 0:
                            continue

                        a, b = line.split(' ')
                        offcpu_regions.append((to_ms(int(a)),
                                               to_ms(int(b))))

            thread_specific_metadata_path = self._path / 'walltime' / \
                pid / tid / 'dirmeta.json'

            if thread_specific_metadata_path.exists():
                with thread_specific_metadata_path.open(mode='r') as f:
                    thread_specific_metadata = json.load(f)
            else:
                thread_specific_metadata = {}

            total_sampled_time = \
                to_ms(thread_specific_metadata.get('sampled_period', None))

            if total_sampled_time is None:
                total_sampled_time = runtime

            to_return = {
                'id': pid_tid.replace('/', '_'),
                'start_time': [start_time],
                'runtime': [runtime],
                'sampled_time': total_sampled_time,
                'name': process_name,
                'pid_tid': pid_tid,
                'off_cpu': offcpu_regions,
                'start_callchain': self._threads_metadata[
                    'spawning_callchains'].get(
                    tid, []),
                'metrics': [] if self._metadata['regions_only'] else self._metrics,
                'children': []
            }

            if is_root:
                to_return['general_metrics'] = self._general_metrics
                to_return['src'] = self.get_sources()
                to_return['src_index'] = self.get_source_index()
                to_return['roofline'] = self._roofline_info
                to_return['regions_only'] = self._metadata['regions_only']

            code_regions_path = self._path / 'walltime' / pid / tid / 'regions.dat'

            if code_regions_path.exists():
                code_regions = defaultdict(list)
                with code_regions_path.open(mode='r') as f:
                    for i, line in enumerate(f):
                        line = line.strip()

                        if len(line) == 0:
                            continue

                        m = re.search(r'^(.+) (\d+) (\d+)$', line)

                        if m is None:
                            print(f'Warning: Invalid line {i + 1} in {str(code_regions_path)}',
                                  file=sys.stderr)
                            continue

                        code_regions[m.group(1)].append((int(m.group(2)),
                                                         int(m.group(3))))

                for name, times in code_regions.items():
                    sampled_time = \
                        to_ms(thread_specific_metadata.get('sampled_period_' + name, None))

                    if sampled_time is None:
                        sampled_time = length

                    starts = []
                    lengths = []
                    off_cpus = []

                    for start, length in times:
                        start_num = to_ms(int(start))
                        length_num = to_ms(int(length))

                        starts.append(start_num)
                        lengths.append(length_num)

                        for off_cpu_start, off_cpu_length in offcpu_regions:
                            if off_cpu_start >= start_num and \
                               off_cpu_start <= start_num + length_num:
                                off_cpus.append((off_cpu_start,
                                                 off_cpu_length))

                    code_region_item = {
                        'id': pid_tid.replace('/', '_') + '_' + name,
                        'start_time': starts,
                        'runtime': lengths,
                        'sampled_time': sampled_time,
                        'name': name,
                        'pid_tid': pid_tid,
                        'off_cpu': off_cpus,
                        'metrics': self._metrics,
                        'children': []
                    }

                    to_return['children'].append(code_region_item)

            children = tree.children(node.identifier)

            for child in children:
                to_return['children'].append(node_to_dict(child, False))

            return to_return

        if tree.root is None:
            return json.dumps({})
        else:
            return json.dumps(node_to_dict(tree.get_node(tree.root),
                                           True))

    @Module.needs_loading
    def get_sources(self):
        """
        Get the dictionary mapping library/executable offsets to
        lines within source code files. It can be empty.

        The structure is as follows:
        {
          "<library/executable path>": {
            "<hex offset>": {
              "file": "<path>",
              "line": <number>
            }
          }
        }

        Use get_source_code() along with get_source_index() to obtain
        a source code corresponding to <path>.
        """
        return self._sources

    @Module.needs_loading
    def get_source_index(self):
        """
        Get the dictionary mapping source code paths from get_sources()
        to shortened filenames that should be provided to get_source_code().
        It can be empty.
        """
        return self._source_index

    @Module.needs_loading
    def get_source_code(self, filename):
        """
        Get a source code stored in the module results under a specified
        name.

        :param str filename: Name of a source code to be
                             obtained. It must come from get_source_index().
        """
        if self._source_zip_path is None:
            return None

        with ZipFile(self._source_zip_path) as zip:
            path = ZipFilePath(zip, filename)

            if not path.exists():
                return None

            with path.open() as f:
                return f.read()

    def process_post_request(self, data):
        """
        Please see the REST API documentation of the linuxperf
        module for the structure of POST requests here.
        """
        if 'thread_tree' in data or \
           'general_analysis' in data or \
           ('pid' in data and 'tid' in data and
            'threshold' in data) or \
           'callchain' in data or 'src' in data:
            if 'thread_tree' in data:
                return self.get_json_tree()
            elif 'general_analysis' in data:
                json_data = self.get_general_analysis(
                    data['general_analysis'])

                if json_data is None:
                    return '', 404
                else:
                    return json_data
            elif 'pid' in data and 'tid' in data and \
                 'threshold' in data:
                json_data = self.get_flame_graph(
                    data['pid'],
                    data['tid'],
                    float(data['threshold']),
                    data.get('region', None))

                if json_data is None:
                    return '', 404
                else:
                    return json_data
            elif 'callchain' in data:
                return json.dumps(self.get_callchain_mappings())
            elif 'src' in data:
                result = self.get_source_code(data['src'])

                if result is None:
                    return '', 404

                return result

        return '', 400


def get_mod_obj(session_id, entity, analysable, options):
    return LinuxperfModule(session_id, entity, analysable)
