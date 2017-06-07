import os
import sys
import shlex
import glob
import csv
import time
import datetime
import subprocess
from collections import OrderedDict
import logging
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from VAPr import definitions

logger = logging.getLogger()
logger.setLevel(logging.INFO)
try:
    logger.handlers[0].stream = sys.stdout     # Enables logging on jupyter notebooks
except:
    pass

__author__ = 'Carlo Mazzaferro<cmazzafe@ucsd.edu>'


class AnnovarWrapper:

    """ Wrapper around Annovar download and annotation functions """

    def __init__(self,
                 input_dir,
                 output_csv_path,
                 annovar_path,
                 project_data,
                 mapping,
                 design_file=None,
                 build_ver=None):

        """ Project data """
        self.input_dir = input_dir
        self.output_csv_path = output_csv_path
        self.annovar = annovar_path
        self.project_data = project_data
        self.design_file = design_file
        self.buildver = build_ver
        self.mapping = mapping
        """ Databases data """
        self.down_dd = definitions.down_dd
        self.annovar_hosted = definitions.annovar_hosted
        self.dl_list_command = definitions.dl_list_command
        self.manual_update = definitions.manual_update
        self.hg_18_databases = definitions.hg_18_databases
        self.hg_19_databases = definitions.hg_19_databases
        self.hg_38_databases = definitions.hg_38_databases
        self.databases = self.get_databases()

    def download_dbs(self, all_dbs=True, dbs=None):
        """
        Implementation of the wrapper around annotate_variation.pl with -downdb as optional arg
        First, it cleans up the humandb/ directory to avoid conflicts, then gets newest versions of databases
        by spawning the jobs using subprocesses

        """

        if len(os.listdir(os.path.join(self.annovar, 'humandb/'))) > 0:
            files = glob.glob(os.path.join(self.annovar, 'humandb/*'))
            for f in files:
                os.remove(f)

        list_commands = self.build_db_dl_command_str(all_dbs, dbs)
        for command in list_commands:
            args = shlex.split(command)
            # Spawn subprocesses
            subprocess.Popen(args, stdout=subprocess.PIPE)
            run_handler(os.path.join(self.annovar, 'humandb/'), cmds=list_commands, annovar_path=self.annovar)

        return 'Finished downloading databases to {}'.format(os.path.join(self.annovar, 'humandb/'))

    def run_annovar(self, batch_jobs=10, multisample=False):
        """ Spawning Annovar jobs in batches of five files at a time to prevent memory overflow """

        chunks = int(len(self.mapping)/batch_jobs) + 1
        n_job_splits = [(i*batch_jobs, (i+1)*batch_jobs) for i in range(chunks)]
        n_files_created = 0

        for index, job in enumerate(n_job_splits):
            logging.info('Job %i/%i sent for processing' % (index + 1 , len(n_job_splits)))
            n_files_created += len(self.mapping[job[0]:job[1]])

            for index, _map in enumerate(self.mapping[job[0]:job[1]]):
                annotation_dir = _map['csv_file_full_path']

                if os.path.isdir(annotation_dir):
                    logging.info('Directory already exists for %s. '
                                 'Writing output files there for file %s.' % (annotation_dir,
                                                                              _map['raw_vcf_file_full_path']))
                else:
                    os.makedirs(annotation_dir)

                vcf_path = _map['raw_vcf_file_full_path']
                csv_path = os.path.join(_map['csv_file_full_path'], _map['csv_file_basename'])
                cmd_string = self.build_annovar_command_str(vcf_path, csv_path, multisample=multisample)
                args = shlex.split(cmd_string)

                subprocess.Popen(args, stdout=subprocess.PIPE)

            logging.info('Annovar jobs submitted for files %s' %
                         ', '.join([i['raw_vcf_file_full_path'] for i in self.mapping]))

            listen(self.output_csv_path, batch_jobs, job[1])
            logging.info('Finished running Annovar on this batch')

        logging.info('Finished running Annovar on all files')

    def build_annovar_command_str(self, vcf, csv, multisample=False):
        """ Concatenate command string arguments for Annovar jobs """

        dbs = ",".join(list(self.databases.keys()))
        dbs_args = ",".join(list(self.databases.values()))

        if '1000g2015aug' in dbs:
            dbs = dbs.replace('1000g2015aug', '1000g2015aug_all')
        command = " ".join(['perl', os.path.join(self.annovar, 'table_annovar.pl'), vcf,
                            os.path.join(self.annovar, 'humandb/'), '-buildver', self.buildver, '-out',
                            csv, '-remove -protocol', dbs,  '-operation',
                            dbs_args, '-nastring .', '-otherinfo -vcfinput'])
        if multisample:
            command += ' -format vcf4 -allsample -withfreq'

        return command

    def build_db_dl_command_str(self, all_dbs, dbs):
        """ Concatenate command string arguments for Annovar download database jobs """

        if not all_dbs:
            for db in dbs:
                if db not in self.databases:
                    raise ValueError('Database %s not supported for build version %s' % (db, self.buildver))
            self.databases = {db: self.databases[db] for db in dbs}

        command_list = []

        for db in self.databases:

            if self.annovar_hosted[db]:
                command_list.append(" ".join(['perl', self.annovar + 'annotate_variation.pl', '-build', self.buildver,
                                              '-downdb', self.down_dd, db, self.annovar + 'humandb/']))
            else:
                command_list.append(" ".join(['perl', self.annovar + 'annotate_variation.pl', '-build', self.buildver,
                                              '-downdb', db, self.annovar + 'humandb/']))
        return command_list

    def check_for_database_updates(self):
        """ Deprecated """

        self.download_dbs(all_dbs=False, dbs=['avdblist'])

        with open(os.path.join(self.annovar, '/humandb' + self.buildver + '_avdblist.txt'), 'r') as db_list:
            reader = csv.reader(db_list, delimiter='\t')
            db_dict = {}
            for i in reader:

                if i[0][-6:] != 'idx.gz':
                    db_dict[i[0][5:]] = [datetime.datetime(int(i[1][0:4]), int(i[1][4:6]), int(i[1][6:8])), i[2]]

        for db in self.manual_update.keys():
            for db_ in db_dict.keys():
                if db_.startswith(db):
                    if db_dict[db_][0] > self.manual_update[db][0]:
                        logging.info('Database %s outdated, will download newer version' % db_)
                        self.download_dbs(all_dbs=False, dbs=[os.path.splitext(os.path.splitext(db_)[0])[0]])

    def get_databases(self):

        if self.buildver == 'hg18':
            databases = self.hg_18_databases
        elif self.buildver == 'hg19':
            databases = self.hg_19_databases
        else:
            databases = self.hg_38_databases

        return databases


def listen(out_path, batch_jobs, n_files):
    """ Little function to check for newly created annotated files """

    added = 0

    while True:
        txts = []
        walker = os.walk(out_path)
        for folder, subfolders, files in walker:
            for _file in files:
                if _file.endswith('txt'):
                    txts.append(_file)

        time.sleep(5)

        new_files = []
        walker = os.walk(out_path)
        for folder, subfolders, files in walker:
            for _file in files:
                if _file.endswith('txt'):
                    new_files.append(_file)

        newly_created = [x for x in new_files if x not in txts]

        if len(newly_created) > 0:
            added += 1
            logging.info('File %i/%i: Annovar finished working on file : ' % (added, n_files) +
                         os.path.basename(newly_created[0]) +
                         '.\n A text file has been created in the %s directory\n' % out_path)

        if added == batch_jobs:
            break
        if len(txts) >= n_files:
            break


class MyHandler(FileSystemEventHandler):
    """
    Overwrite the methods for creation of files as annovar runs. Once the .csv file is detected, exit process
    and proceed to next file.
    """

    def __init__(self, observer, cmds=None, annovar_path='ANNOVAR_PATH'):

        self.observer = observer
        self.annovar = annovar_path
        self.cmds = cmds

    def on_created(self, event):

        logging.info('Downloading: ' + event.src_path)

        if self.cmds[-1][-2] in event.src_path:
            self.observer.stop()


def run_handler(output_csv_path, cmds=None, annovar_path='ANNOVAR_PATH'):
    observer = Observer()
    event_handler = MyHandler(observer,
                              cmds=cmds,
                              annovar_path=annovar_path)

    observer.schedule(event_handler, output_csv_path, recursive=True)
    observer.start()
    observer.join()

    return None