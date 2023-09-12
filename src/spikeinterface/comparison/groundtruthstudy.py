from pathlib import Path
import shutil
import os
import json
import pickle

import numpy as np

from spikeinterface.core import load_extractor, extract_waveforms, load_waveforms
from spikeinterface.core.core_tools import SIJsonEncoder

from spikeinterface.sorters import run_sorter_jobs, read_sorter_folder

from spikeinterface import WaveformExtractor
from spikeinterface.qualitymetrics import compute_quality_metrics

from .paircomparisons import compare_sorter_to_ground_truth, GroundTruthComparison

# from .studytools import (
#     setup_comparison_study,
#     get_rec_names,
#     get_recordings,
#     iter_working_folder,
#     iter_computed_names,
#     iter_computed_sorting,
#     collect_run_times,
# )


# TODO : save comparison in folders
# TODO : find a way to set level names



# This is to separate names when the key are tuples when saving folders
_key_separator = " ## "
# This would be more funny
# _key_separator = " (°_°) "


class GroundTruthStudy:
    """
    This class is an helper function to run any comparison on several "cases" for several ground truth dataset.

    "cases" can be:
      * several sorter for comparisons
      * same sorter with differents parameters
      * parameters of comparisons
      * any combination of theses
    
      For enough flexibility cases key can be a tuple so that we can varify complexity along several
      "axis" (paremeters or sorter)
    
    Ground truth dataset need recording+sorting. This can be from meraec file or from the internal generator
    :py:fun:`generate_ground_truth_recording()`
    
    This GroundTruthStudy have been refactor in version 0.100 to be more flexible than previous versions.
    Folders structures are not backward compatible.


    
    """
    def __init__(self, study_folder):
        self.folder = Path(study_folder)

        self.datasets = {}
        self.cases = {}
        self.sortings = {}
        self.comparisons = {}

        self.scan_folder()

    @classmethod
    def create(cls, study_folder, datasets={}, cases={}, levels=None):

        # check that cases keys are homogeneous
        key0 = list(cases.keys())[0]
        if isinstance(key0, str):
            assert all(isinstance(key, str) for key in cases.keys()), "Keys for cases are not homogeneous"
            if levels is None:
                levels = "level0"
            else:
                assert isinstance(levels, str)
        elif isinstance(key0, tuple):
            assert all(isinstance(key, tuple) for key in cases.keys()), "Keys for cases are not homogeneous"
            num_levels = len(key0)
            assert all(len(key) == num_levels for key in cases.keys()), "Keys for cases are not homogeneous, tuple negth differ"
            if levels is None:
                levels = [f"level{i}" for i in range(num_levels)]
            else:
                levels = list(levels)
                assert len(levels) == num_levels
        else:
            raise ValueError("Keys for cases must str or tuple")


        study_folder = Path(study_folder)
        study_folder.mkdir(exist_ok=False, parents=True)

        (study_folder / "datasets").mkdir()
        (study_folder / "datasets/recordings").mkdir()
        (study_folder / "datasets/gt_sortings").mkdir()
        (study_folder / "sorters").mkdir()
        (study_folder / "sortings").mkdir()
        (study_folder / "sortings" / "run_logs").mkdir()
        (study_folder / "metrics").mkdir()



        for key, (rec, gt_sorting) in datasets.items():
            assert "/" not in key
            assert "\\" not in key

            # rec are pickle
            rec.dump_to_pickle(study_folder / f"datasets/recordings/{key}.pickle")

            # sorting are pickle + saved as NumpyFolderSorting
            gt_sorting.dump_to_pickle(study_folder / f"datasets/gt_sortings/{key}.pickle")
            gt_sorting.save(format="numpy_folder", folder=study_folder / f"datasets/gt_sortings/{key}")
        
        
        info = {}
        info["levels"] = levels
        (study_folder / "info.json").write_text(json.dumps(info, indent=4), encoding="utf8")

        # (study_folder / "cases.jon").write_text(
        #     json.dumps(cases, indent=4, cls=SIJsonEncoder),
        #     encoding="utf8",
        # )
        # cases is dump to a pickle file, json is not possible because of tuple key
        (study_folder / "cases.pickle").write_bytes(pickle.dumps(cases))

        return cls(study_folder)


    def scan_folder(self):
        if not (self.folder / "datasets").exists():
            raise ValueError(f"This is folder is not a GroundTruthStudy : {self.folder.absolute()}")

        with open(self.folder / "info.json", "r") as f:
            self.info = json.load(f)
        
        self.levels = self.info["levels"]
        # if isinstance(self.levels, list):
        #     # because tuple caoont be stored in json
        #     self.levels = tuple(self.info["levels"])

        for rec_file in (self.folder / "datasets/recordings").glob("*.pickle"):
            key = rec_file.stem
            rec = load_extractor(rec_file)
            gt_sorting = load_extractor(self.folder / f"datasets/gt_sortings/{key}")
            self.datasets[key] = (rec, gt_sorting)
        
        with open(self.folder / "cases.pickle", "rb") as f:
            self.cases = pickle.load(f)

        self.comparisons = {k: None for k in self.cases}

        self.sortings = {}
        for key in self.cases:
            sorting_folder = self.folder / "sortings" / self.key_to_str(key)
            if sorting_folder.exists():
                sorting = load_extractor(sorting_folder)
            else:
                sorting = None
            self.sortings[key] = sorting


    def __repr__(self):
        t = f"GroundTruthStudy {self.folder.stem} \n"
        t += f"  datasets: {len(self.datasets)} {list(self.datasets.keys())}\n"
        t += f"  cases: {len(self.cases)} {list(self.cases.keys())}\n"
        num_computed = sum([1 for sorting in self.sortings.values() if sorting is not None])
        t += f"  computed: {num_computed}\n"

        return t

    def key_to_str(self, key):
        if isinstance(key, str):
            return key
        elif isinstance(key, tuple):
            return _key_separator.join(key)
        else:
            raise ValueError("Keys for cases must str or tuple")

    def run_sorters(self, case_keys=None, engine='loop', engine_kwargs={}, keep=True, verbose=False):
        """
        
        """
        if case_keys is None:
            case_keys = self.cases.keys()

        job_list = []
        for key in case_keys:
            sorting_folder = self.folder / "sortings" / self.key_to_str(key)
            sorting_exists = sorting_folder.exists()

            sorter_folder = self.folder / "sorters" / self.key_to_str(key)
            sorter_folder_exists = sorting_folder.exists()

            if keep:
                if sorting_exists:
                    continue
                if sorter_folder_exists:
                    # the sorter folder exists but havent been copied to sortings folder
                    sorting = read_sorter_folder(sorter_folder, raise_error=False)
                    if sorting is not None:
                        # save and skip
                        self.copy_sortings(case_keys=[key])
                        continue

            if sorting_exists:
                # TODO : delete sorting + log
                pass

            params = self.cases[key]["run_sorter_params"].copy()
            # this ensure that sorter_name is given
            recording, _ = self.datasets[self.cases[key]["dataset"]]
            sorter_name = params.pop("sorter_name")
            job = dict(sorter_name=sorter_name,
                       recording=recording,
                       output_folder=sorter_folder)
            job.update(params)
            # the verbose is overwritten and global to all run_sorters
            job["verbose"] = verbose
            job_list.append(job)

        run_sorter_jobs(job_list, engine=engine, engine_kwargs=engine_kwargs, return_output=False)

        # TODO create a list in laucher for engine blocking and non-blocking
        if engine not in ("slurm", ):
            self.copy_sortings(case_keys)

    def copy_sortings(self, case_keys=None, force=True):
        if case_keys is None:
            case_keys = self.cases.keys()
        
        for key in case_keys:
            sorting_folder = self.folder / "sortings" / self.key_to_str(key)
            sorter_folder = self.folder / "sorters" / self.key_to_str(key)
            log_file = self.folder / "sortings" / "run_logs" / f"{self.key_to_str(key)}.json"

            sorting = read_sorter_folder(sorter_folder, raise_error=False)
            if sorting is not None:
                if sorting_folder.exists():
                    if force:
                        # TODO delete folder + log
                        shutil.rmtree(sorting_folder)
                    else:
                        continue

                sorting = sorting.save(format="numpy_folder", folder=sorting_folder)
                self.sortings[key] = sorting

                # copy logs
                shutil.copyfile(sorter_folder / "spikeinterface_log.json", log_file)

    def run_comparisons(self, case_keys=None, comparison_class=GroundTruthComparison, **kwargs):

        if case_keys is None:
            case_keys = self.cases.keys()

        for key in case_keys:
            dataset_key = self.cases[key]["dataset"]
            _, gt_sorting = self.datasets[dataset_key]
            sorting = self.sortings[key]
            if sorting is None:
                self.comparisons[key] = None    
                continue
            comp = comparison_class(gt_sorting, sorting, **kwargs)
            self.comparisons[key] = comp

    def get_run_times(self, case_keys=None):
        import pandas as pd
        if case_keys is None:
            case_keys = self.cases.keys()

        log_folder = self.folder / "sortings" / "run_logs"
        
        run_times = {}
        for key in case_keys:
            log_file = log_folder / f"{self.key_to_str(key)}.json"
            with open(log_file, mode="r") as logfile:
                log = json.load(logfile)
                run_time = log.get("run_time", None)
            run_times[key] = run_time

        return pd.Series(run_times, name="run_time")

    def extract_waveforms_gt(self, case_keys=None, **extract_kwargs):

        if case_keys is None:
            case_keys = self.cases.keys()

        base_folder = self.folder / "waveforms"
        base_folder.mkdir(exist_ok=True)

        for key in case_keys:
            dataset_key = self.cases[key]["dataset"]
            recording, gt_sorting = self.datasets[dataset_key]
            wf_folder = base_folder / self.key_to_str(key)
            we = extract_waveforms(recording, gt_sorting, folder=wf_folder)

    def get_waveform_extractor(self, key):
        # some recording are not dumpable to json and the waveforms extactor need it!
        # so we load it with and put after
        we = load_waveforms(self.folder / "waveforms" / self.key_to_str(key), with_recording=False)
        dataset_key = self.cases[key]["dataset"]
        recording, _ = self.datasets[dataset_key]        
        we.set_recording(recording)
        return we

    def get_templates(self, key, mode="mean"):
        we = self.get_waveform_extractor(key)
        templates = we.get_all_templates(mode=mode)
        return templates

    def compute_metrics(self, case_keys=None, metric_names=["snr", "firing_rate"], force=False):
        if case_keys is None:
            case_keys = self.cases.keys()
        
        for key in case_keys:
            filename = self.folder / "metrics" / f"{self.key_to_str(key)}.txt"
            if filename.exists():
                if force:
                    os.remove(filename)
                else:
                    continue

            we = self.get_waveform_extractor(key)
            metrics = compute_quality_metrics(we, metric_names=metric_names)
            metrics.to_csv(filename, sep="\t", index=True)

    def get_metrics(self, key):
        import pandas  as pd
        filename = self.folder / "metrics" / f"{self.key_to_str(key)}.txt"
        if not filename.exists():
            return
        metrics = pd.read_csv(filename, sep="\t", index_col=0)
        dataset_key = self.cases[key]["dataset"]
        recording, gt_sorting = self.datasets[dataset_key]        
        metrics.index = gt_sorting.unit_ids
        return metrics
            
    def get_units_snr(self, key):
        """
        """
        return self.get_metrics(key)["snr"]

    def get_performance_by_unit(self, case_keys=None):

        import pandas as pd

        if case_keys is None:
            case_keys = self.cases.keys()

        perf_by_unit = []
        for key in case_keys:
            comp = self.comparisons.get(key, None)
            assert comp is not None, "You need to do study.run_comparisons() first"

            perf = comp.get_performance(method="by_unit", output="pandas")
            if isinstance(key, str):
                perf[self.levels] = key
            elif isinstance(key, tuple):
                for col, k in zip(self.levels, key):
                    perf[col] = k
              
            perf = perf.reset_index()
            perf_by_unit.append(perf)

        

        perf_by_unit = pd.concat(perf_by_unit)
        perf_by_unit = perf_by_unit.set_index(self.levels)
        return perf_by_unit

    def get_count_units(
            self, case_keys=None, well_detected_score=None, redundant_score=None, overmerged_score=None
        ):

        import pandas as pd

        if case_keys is None:
            case_keys = list(self.cases.keys())

        if isinstance(case_keys[0], str):
            index = pd.Index(case_keys, name=self.levels)
        else:
            index = pd.MultiIndex.from_tuples(case_keys, names=self.levels)


        columns = ["num_gt", "num_sorter", "num_well_detected", "num_redundant", "num_overmerged"]
        comp = self.comparisons[case_keys[0]]
        if comp.exhaustive_gt:
            columns.extend(["num_false_positive", "num_bad"])
        count_units = pd.DataFrame(index=index, columns=columns, dtype=int)


        for key in case_keys:
            comp = self.comparisons.get(key, None)
            assert comp is not None, "You need to do study.run_comparisons() first"

            gt_sorting = comp.sorting1
            sorting = comp.sorting2

            count_units.loc[key, "num_gt"] = len(gt_sorting.get_unit_ids())
            count_units.loc[key, "num_sorter"] = len(sorting.get_unit_ids())
            count_units.loc[key, "num_well_detected"] = comp.count_well_detected_units(
                well_detected_score
            )
            if comp.exhaustive_gt:
                count_units.loc[key, "num_overmerged"] = comp.count_overmerged_units(
                    overmerged_score
                )
                count_units.loc[key, "num_redundant"] = comp.count_redundant_units(redundant_score)
                count_units.loc[key, "num_false_positive"] = comp.count_false_positive_units(
                    redundant_score
                )
                count_units.loc[key, "num_bad"] = comp.count_bad_units()

        return count_units





class OLDGroundTruthStudy:
    def __init__(self, study_folder=None):
        import pandas as pd

        self.study_folder = Path(study_folder)
        self._is_scanned = False
        self.computed_names = None
        self.rec_names = None
        self.sorter_names = None

        self.scan_folder()

        self.comparisons = None
        self.exhaustive_gt = None

    def __repr__(self):
        t = "Ground truth study\n"
        t += "  " + str(self.study_folder) + "\n"
        t += "  recordings: {} {}\n".format(len(self.rec_names), self.rec_names)
        if len(self.sorter_names):
            t += "  sorters: {} {}\n".format(len(self.sorter_names), self.sorter_names)

        return t

    def scan_folder(self):
        self.rec_names = get_rec_names(self.study_folder)
        # scan computed names
        self.computed_names = list(iter_computed_names(self.study_folder))  # list of pair (rec_name, sorter_name)
        self.sorter_names = np.unique([e for _, e in iter_computed_names(self.study_folder)]).tolist()
        self._is_scanned = True

    @classmethod
    def create(cls, study_folder, gt_dict, **job_kwargs):
        setup_comparison_study(study_folder, gt_dict, **job_kwargs)
        return cls(study_folder)

    def run_sorters(self, sorter_list, mode_if_folder_exists="keep", remove_sorter_folders=False, **kwargs):
        sorter_folders = self.study_folder / "sorter_folders"
        recording_dict = get_recordings(self.study_folder)

        run_sorters(
            sorter_list,
            recording_dict,
            sorter_folders,
            with_output=False,
            mode_if_folder_exists=mode_if_folder_exists,
            **kwargs,
        )

        # results are copied so the heavy sorter_folders can be removed
        self.copy_sortings()

        if remove_sorter_folders:
            shutil.rmtree(self.study_folder / "sorter_folders")

    def _check_rec_name(self, rec_name):
        if not self._is_scanned:
            self.scan_folder()
        if len(self.rec_names) > 1 and rec_name is None:
            raise Exception("Pass 'rec_name' parameter to select which recording to use.")
        elif len(self.rec_names) == 1:
            rec_name = self.rec_names[0]
        else:
            rec_name = self.rec_names[self.rec_names.index(rec_name)]
        return rec_name

    def get_ground_truth(self, rec_name=None):
        rec_name = self._check_rec_name(rec_name)
        sorting = load_extractor(self.study_folder / "ground_truth" / rec_name)
        return sorting

    def get_recording(self, rec_name=None):
        rec_name = self._check_rec_name(rec_name)
        rec = load_extractor(self.study_folder / "raw_files" / rec_name)
        return rec

    def get_sorting(self, sort_name, rec_name=None):
        rec_name = self._check_rec_name(rec_name)

        selected_sorting = None
        if sort_name in self.sorter_names:
            for r_name, sorter_name, sorting in iter_computed_sorting(self.study_folder):
                if sort_name == sorter_name and r_name == rec_name:
                    selected_sorting = sorting
        return selected_sorting

    def copy_sortings(self):
        sorter_folders = self.study_folder / "sorter_folders"
        sorting_folders = self.study_folder / "sortings"
        log_olders = self.study_folder / "sortings" / "run_log"

        log_olders.mkdir(parents=True, exist_ok=True)

        for rec_name, sorter_name, output_folder in iter_working_folder(sorter_folders):
            SorterClass = sorter_dict[sorter_name]
            fname = rec_name + "[#]" + sorter_name
            npz_filename = sorting_folders / (fname + ".npz")

            try:
                sorting = SorterClass.get_result_from_folder(output_folder)
                NpzSortingExtractor.write_sorting(sorting, npz_filename)
            except:
                if npz_filename.is_file():
                    npz_filename.unlink()
            if (output_folder / "spikeinterface_log.json").is_file():
                shutil.copyfile(
                    output_folder / "spikeinterface_log.json", sorting_folders / "run_log" / (fname + ".json")
                )

        self.scan_folder()

    def run_comparisons(self, exhaustive_gt=False, **kwargs):
        self.comparisons = {}
        for rec_name, sorter_name, sorting in iter_computed_sorting(self.study_folder):
            gt_sorting = self.get_ground_truth(rec_name)
            sc = compare_sorter_to_ground_truth(gt_sorting, sorting, exhaustive_gt=exhaustive_gt, **kwargs)
            self.comparisons[(rec_name, sorter_name)] = sc
        self.exhaustive_gt = exhaustive_gt

    def aggregate_run_times(self):
        return collect_run_times(self.study_folder)

    def aggregate_performance_by_unit(self):
        assert self.comparisons is not None, "run_comparisons first"

        perf_by_unit = []
        for rec_name, sorter_name, sorting in iter_computed_sorting(self.study_folder):
            comp = self.comparisons[(rec_name, sorter_name)]

            perf = comp.get_performance(method="by_unit", output="pandas")
            perf["rec_name"] = rec_name
            perf["sorter_name"] = sorter_name
            perf = perf.reset_index()
            perf_by_unit.append(perf)

        import pandas as pd

        perf_by_unit = pd.concat(perf_by_unit)
        perf_by_unit = perf_by_unit.set_index(["rec_name", "sorter_name", "gt_unit_id"])

        return perf_by_unit

    def aggregate_count_units(self, well_detected_score=None, redundant_score=None, overmerged_score=None):
        assert self.comparisons is not None, "run_comparisons first"

        import pandas as pd

        index = pd.MultiIndex.from_tuples(self.computed_names, names=["rec_name", "sorter_name"])

        count_units = pd.DataFrame(
            index=index,
            columns=["num_gt", "num_sorter", "num_well_detected", "num_redundant", "num_overmerged"],
            dtype=int,
        )

        if self.exhaustive_gt:
            count_units["num_false_positive"] = pd.Series(dtype=int)
            count_units["num_bad"] = pd.Series(dtype=int)

        for rec_name, sorter_name, sorting in iter_computed_sorting(self.study_folder):
            gt_sorting = self.get_ground_truth(rec_name)
            comp = self.comparisons[(rec_name, sorter_name)]

            count_units.loc[(rec_name, sorter_name), "num_gt"] = len(gt_sorting.get_unit_ids())
            count_units.loc[(rec_name, sorter_name), "num_sorter"] = len(sorting.get_unit_ids())
            count_units.loc[(rec_name, sorter_name), "num_well_detected"] = comp.count_well_detected_units(
                well_detected_score
            )
            if self.exhaustive_gt:
                count_units.loc[(rec_name, sorter_name), "num_overmerged"] = comp.count_overmerged_units(
                    overmerged_score
                )
                count_units.loc[(rec_name, sorter_name), "num_redundant"] = comp.count_redundant_units(redundant_score)
                count_units.loc[(rec_name, sorter_name), "num_false_positive"] = comp.count_false_positive_units(
                    redundant_score
                )
                count_units.loc[(rec_name, sorter_name), "num_bad"] = comp.count_bad_units()

        return count_units

    def aggregate_dataframes(self, copy_into_folder=True, **karg_thresh):
        dataframes = {}
        dataframes["run_times"] = self.aggregate_run_times().reset_index()
        perfs = self.aggregate_performance_by_unit()

        dataframes["perf_by_unit"] = perfs.reset_index()
        dataframes["count_units"] = self.aggregate_count_units(**karg_thresh).reset_index()

        if copy_into_folder:
            tables_folder = self.study_folder / "tables"
            tables_folder.mkdir(parents=True, exist_ok=True)

            for name, df in dataframes.items():
                df.to_csv(str(tables_folder / (name + ".csv")), sep="\t", index=False)

        return dataframes

    def get_waveform_extractor(self, rec_name, sorter_name=None):
        rec = self.get_recording(rec_name)

        if sorter_name is None:
            name = "GroundTruth"
            sorting = self.get_ground_truth(rec_name)
        else:
            assert sorter_name in self.sorter_names
            name = sorter_name
            sorting = self.get_sorting(sorter_name, rec_name)

        waveform_folder = self.study_folder / "waveforms" / f"waveforms_{name}_{rec_name}"

        if waveform_folder.is_dir():
            we = WaveformExtractor.load(waveform_folder)
        else:
            we = WaveformExtractor.create(rec, sorting, waveform_folder)
        return we

    def compute_waveforms(
        self,
        rec_name,
        sorter_name=None,
        ms_before=3.0,
        ms_after=4.0,
        max_spikes_per_unit=500,
        n_jobs=-1,
        total_memory="1G",
    ):
        we = self.get_waveform_extractor(rec_name, sorter_name)
        we.set_params(ms_before=ms_before, ms_after=ms_after, max_spikes_per_unit=max_spikes_per_unit)
        we.run_extract_waveforms(n_jobs=n_jobs, total_memory=total_memory)

    def get_templates(self, rec_name, sorter_name=None, mode="median"):
        """
        Get template for a given recording.

        If sorter_name=None then template are from the ground truth.

        """
        we = self.get_waveform_extractor(rec_name, sorter_name=sorter_name)
        templates = we.get_all_templates(mode=mode)
        return templates

    def compute_metrics(
        self,
        rec_name,
        metric_names=["snr"],
        ms_before=3.0,
        ms_after=4.0,
        max_spikes_per_unit=500,
        n_jobs=-1,
        total_memory="1G",
    ):
        we = self.get_waveform_extractor(rec_name)
        we.set_params(ms_before=ms_before, ms_after=ms_after, max_spikes_per_unit=max_spikes_per_unit)
        we.run_extract_waveforms(n_jobs=n_jobs, total_memory=total_memory)

        # metrics
        metrics = compute_quality_metrics(we, metric_names=metric_names)
        folder = self.study_folder / "metrics"
        folder.mkdir(exist_ok=True)
        filename = folder / f"metrics _{rec_name}.txt"
        metrics.to_csv(filename, sep="\t", index=True)

        return metrics

    def get_metrics(self, rec_name=None, **metric_kwargs):
        """
        Load or compute units metrics  for a given recording.
        """
        rec_name = self._check_rec_name(rec_name)
        metrics_folder = self.study_folder / "metrics"
        metrics_folder.mkdir(parents=True, exist_ok=True)

        filename = self.study_folder / "metrics" / f"metrics _{rec_name}.txt"
        import pandas as pd

        if filename.is_file():
            metrics = pd.read_csv(filename, sep="\t", index_col=0)
            gt_sorting = self.get_ground_truth(rec_name)
            metrics.index = gt_sorting.unit_ids
        else:
            metrics = self.compute_metrics(rec_name, **metric_kwargs)

        metrics.index.name = "unit_id"
        #  add rec name columns
        metrics["rec_name"] = rec_name

        return metrics

    def get_units_snr(self, rec_name=None, **metric_kwargs):
        """ """
        metric = self.get_metrics(rec_name=rec_name, **metric_kwargs)
        return metric["snr"]

    def concat_all_snr(self):
        metrics = []
        for rec_name in self.rec_names:
            df = self.get_metrics(rec_name)
            df = df.reset_index()
            metrics.append(df)
        metrics = pd.concat(metrics)
        metrics = metrics.set_index(["rec_name", "unit_id"])
        return metrics["snr"]
