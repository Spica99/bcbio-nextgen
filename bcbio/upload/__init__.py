"""Handle extraction of final files from processing pipelines into storage.
"""
import datetime
import os

from bcbio import utils
from bcbio.upload import shared, filesystem, galaxy, s3
from bcbio.pipeline import run_info

_approaches = {"filesystem": filesystem,
               "galaxy": galaxy,
               "s3": s3}

def from_sample(sample):
    """Upload results of processing from an analysis pipeline sample.
    """
    upload_config = sample.get("upload")
    if upload_config:
        approach = _approaches[upload_config.get("method", "filesystem")]
        for finfo in _get_files(sample):
            approach.update_file(finfo, sample, upload_config)
        for finfo in _get_files_project(sample, upload_config):
            approach.update_file(finfo, None, upload_config)

# ## File information from sample

def _get_files(sample):
    """Retrieve files for the sample, dispatching by analysis type.

    Each file is a dictionary containing the path plus associated
    metadata about the file and pipeline versions.
    """
    analysis = sample.get("analysis")
    if analysis.lower() in ["variant", "snp calling", "variant2", "standard"]:
        return _get_files_variantcall(sample)
    elif analysis in ["RNA-seq"]:
        return _get_files_rnaseq(sample)
    elif analysis.lower() in ["chip-seq"]:
        return _get_files_chipseq(sample)
    else:
        return []

def _get_files_rnaseq(sample):
    out = []
    algorithm = sample["config"]["algorithm"]
    out = _maybe_add_summary(algorithm, sample, out)
    out = _maybe_add_alignment(algorithm, sample, out)
    out = _maybe_add_counts(algorithm, sample, out)
    out = _maybe_add_cufflinks(algorithm, sample, out)
    return _add_meta(out, sample)

def _get_files_chipseq(sample):
    out = []
    algorithm = sample["config"]["algorithm"]
    out = _maybe_add_summary(algorithm, sample, out)
    out = _maybe_add_alignment(algorithm, sample, out)
    return _add_meta(out, sample)

def _add_meta(xs, sample=None, config=None):
    out = []
    for x in xs:
        x["mtime"] = shared.get_file_timestamp(x["path"])
        if sample and "sample" not in x:
            if isinstance(sample["name"], (tuple, list)):
                name = sample["name"][-1]
            else:
                name = "%s-%s" % (sample["name"], run_info.clean_name(sample["description"]))
            x["sample"] = name
        if config:
            if "fc_name" in config and "fc_date" in config:
                x["run"] = "%s_%s" % (config["fc_date"], config["fc_name"])
            else:
                x["run"] = "project_%s" % datetime.datetime.now().strftime("%Y-%m-%d")
        out.append(x)
    return out

def _get_files_variantcall(sample):
    """Return output files for the variant calling pipeline.
    """
    out = []
    algorithm = sample["config"]["algorithm"]
    out = _maybe_add_summary(algorithm, sample, out)
    out = _maybe_add_alignment(algorithm, sample, out)
    out = _maybe_add_variant_file(algorithm, sample, out)
    return _add_meta(out, sample)

def _maybe_add_variant_file(algorithm, sample, out):
    if sample["work_bam"] is not None and sample.get("vrn_file"):
        for x in sample["variants"]:
            out.extend(_get_vcf(x, ("vrn_file",)))
            if x.get("bed_file"):
                out.append({"path": x["bed_file"],
                            "type": "bed",
                            "ext": "%s-callregions" % x["variantcaller"],
                            "variantcaller": x["variantcaller"]})
    return out

def _get_vcf(x, key):
    """Retrieve VCF file with the given key if it exists, handling bgzipped.
    """
    out = []
    fname = utils.get_in(x, key)
    if fname:
        if fname.endswith(".gz"):
            out.append({"path": fname,
                        "type": "vcf.gz",
                        "ext": x["variantcaller"],
                        "variantcaller": x["variantcaller"]})
            if utils.file_exists(fname + ".tbi"):
                out.append({"path": fname + ".tbi",
                            "type": "vcf.gz.tbi",
                            "index": True,
                            "ext": x["variantcaller"],
                            "variantcaller": x["variantcaller"]})
        else:
            out.append({"path": fname,
                        "type": "vcf",
                        "ext": x["variantcaller"],
                        "variantcaller": x["variantcaller"]})
    return out

def _maybe_add_summary(algorithm, sample, out):
    out = []
    if "summary" in sample:
        if sample["summary"].get("pdf"):
            out.append({"path": sample["summary"]["pdf"],
                       "type": "pdf",
                       "ext": "summary"})
        if sample["summary"].get("qc"):
            out.append({"path": sample["summary"]["qc"],
                        "type": "directory",
                        "ext": "qc"})
        if utils.get_in(sample, ("summary", "researcher")):
            out.append({"path": sample["summary"]["researcher"],
                        "type": "tsv",
                        "sample": run_info.clean_name(utils.get_in(sample, ("upload", "researcher"))),
                        "ext": "summary"})
    return out

def _maybe_add_alignment(algorithm, sample, out):
    if _has_alignment_file(algorithm, sample):
        out.append({"path": sample["work_bam"],
                    "type": "bam",
                    "ext": "ready"})
        if utils.file_exists(sample["work_bam"] + ".bai"):
            out.append({"path": sample["work_bam"] + ".bai",
                        "type": "bam.bai",
                        "index": True,
                        "ext": "ready"})
    return out

def _maybe_add_counts(algorithm, sample, out):
    out.append({"path": sample["count_file"],
                "type": "counts",
                "ext": "ready"})
    stats_file = os.path.splitext(sample["count_file"])[0] + ".stats"
    if utils.file_exists(stats_file):
        out.append({"path": stats_file,
                    "type": "count_stats",
                    "ext": "ready"})
    return out

def _maybe_add_cufflinks(algorithm, sample, out):
    if "cufflinks_dir" in sample:
        out.append({"path": sample["cufflinks_dir"],
                    "type": "directory",
                    "ext": "cufflinks"})
    return out

def _has_alignment_file(algorithm, sample):
    return (((algorithm.get("aligner") or algorithm.get("realign")
              or algorithm.get("recalibrate")) and
              algorithm.get("merge_bamprep", True)) and
              sample["work_bam"] is not None)

# ## File information from full project

def _get_files_project(sample, upload_config):
    """Retrieve output files associated with an entire analysis project.
    """
    out = [{"path": sample["provenance"]["programs"]}]
    if "summary" in sample and sample["summary"].get("project"):
        out.append({"path": sample["summary"]["project"]})

    for x in sample.get("variants", []):
        if "pop_db" in x:
            out.append({"path": x["pop_db"],
                        "type": "sqlite",
                        "variantcaller": x["variantcaller"]})
    for x in sample.get("variants", []):
        if "population" in x:
            pop_db = x["population"].get("db")
            if pop_db:
                out.append({"path": pop_db,
                            "type": "sqlite",
                            "variantcaller": x["variantcaller"]})
            out.extend(_get_vcf(x, ("population", "vcf")))
    for x in sample.get("variants", []):
        if x.get("validate") and x["validate"].get("grading_summary"):
            out.append({"path": x["validate"]["grading_summary"]})
            break

    if "combined_counts" in sample:
        out.append({"path": sample["combined_counts"]})
    if "annotated_combined_counts" in sample:
        out.append({"path": sample["annotated_combined_counts"]})

    return _add_meta(out, config=upload_config)
