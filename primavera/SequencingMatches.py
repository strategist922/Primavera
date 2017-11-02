"""Functions and Classes for matching sequencing results to a reference."""

from copy import deepcopy
import matplotlib.pyplot as plt
from dna_features_viewer import (BiopythonTranslator, GraphicRecord,
                                 GraphicFeature)
import numpy as np
from .biotools import (blast_sequences, read_records_from_zip,
                       rotate_circular_record)
from collections import OrderedDict
import matplotlib.gridspec as gridspec
from Bio import SeqIO
from Bio.SeqFeature import SeqFeature, FeatureLocation
import os


class SequencingRead:
    """Class representing a (Sanger) sequencing read.

    It is recommended to build SequencingReads using the `.from_ab1_file`
    method.

    Parameters
    ----------

    read_name
      Name (label or any other ID) of the read

    read_sequence
      An ATGC string


    """

    def __init__(self, read_name=None, read_sequence=None, read_qualities=None,
                 primer=None):
        self.read_name = read_name
        self.primer = primer
        self.read_sequence = read_sequence
        self.read_qualities = read_qualities
        self.average_quality = (None if read_qualities is None else
                                np.mean(read_qualities))

    def __hash__(self):
        return self.name

    @staticmethod
    def from_ab1_file(ab1_file, read_name=None, primer=None):
        """Read a read from Ab1 data.

        ab1_file can be a file path, a file handle, or a Flametree file object.
        """
        if hasattr(ab1_file, "_name_no_extension"): # Flametree
            read_name = ab1_file._name_no_extension
            ab1_file = ab1_file.open('rb')

        if read_name is None:
            read_name = os.path.basename(os.path.splitext(ab1_file)[0])
        record = SeqIO.read(ab1_file, "abi")
        return SequencingRead(
            read_name=read_name,
            primer=primer,
            read_sequence=str(record.seq),
            read_qualities=np.array(record.letter_annotations["phred_quality"])
        )

    @staticmethod
    def list_from_ab1_zipfile(zipfile):
        reads, errors = read_records_from_zip(zipfile)
        return [
            SequencingRead(
                read_name=name,
                read_sequence=str(record.seq),
                read_qualities=np.array(
                    record.letter_annotations["phred_quality"]))
            for (name, record) in sorted(reads.items())
        ], errors



class SequenceMatch:

    def __init__(self, start, end, strand=1, percent=None,
                 read_qualities=None):
        self.start = start
        self.end = end
        self.strand = strand
        self.percent = percent
        self.read_qualities = read_qualities

    @staticmethod
    def from_hsp(hsp, read=None):
        prc = ("%.01f" % (100.0 * hsp.identities / hsp.align_length)) + "%"
        start, end, strand = hsp.sbjct_start, hsp.sbjct_end, 1
        if start > end:
            start, end, strand = end, start, -1
        if read is not None:
            qualities = read.read_qualities[hsp.query_start:hsp.query_end]
        else:
            qualities = None

        return SequenceMatch(start=start, end=end, strand=strand, percent=prc,
                             read_qualities=qualities)

    def rotated(self, n_bases, construct_length):
        if (self.end - n_bases <= 0):
            n_bases = n_bases - construct_length
        elif (self.start - n_bases >= construct_length):
            n_bases = n_bases + construct_length
        return (self + (-n_bases))

    def __add__(self, number):
        new_match = deepcopy(self)
        new_match.start += number
        new_match.end += number
        return new_match

    def __len__(self):
        return self.end - self.start

    def __repr__(self):
        return "Match(%d, %d, %d, %s)" % (self.start, self.end, self.strand,
                                          self.percent)

    def to_Biopython_feature(self):
        return SeqFeature(
            location=FeatureLocation(self.start, self.end, self.strand),
            type="misc_feature",
            qualifiers={"label": self.percent}
        )


class ReadReferenceMatches:
    """Represent all matches between one read from one primer, and a reference.
    """

    def __init__(self, reference, primer_matches=(), read_matches=(),
                 primer=None, read=None):
        self.primer_matches = list(primer_matches)
        self.read_matches = list(read_matches)
        self.reference = reference
        self.primer = primer
        self.read = read

    def remove_read_matches_contained_in_others(self):
        """Remove every read match contained in other matches."""
        matches = sorted(self.read_matches, key=lambda m: m.start)[::-1]
        new_matches = [m for m in matches]
        for i1, m1 in enumerate(matches):
            for m2 in matches[i1 + 1:]:
                if ((m2.start <= m1.start <= m2.end) and
                    (m2.start <= m1.end <= m2.end)):
                    new_matches.remove(m1)
                    break
        self.read_matches = new_matches

    def rotated(self, n_bases):
        if isinstance(self.reference, str):
            rotated_ref = self.reference[n_bases:] + self.reference[n_bases]
        else:
            rotated_ref = rotate_circular_record(self.reference, n_bases)
        return ReadReferenceMatches(
            rotated_ref,
            primer_matches=[
                m.rotated(n_bases, len(self.reference))
                for m in self.primer_matches
            ],
            read_matches=[
                m.rotated(n_bases, len(self.reference))
                for m in self.read_matches
            ]
        )

    @property
    def primer_gap(self):
        if len(self.read_matches) == 0:
            raise ValueError("Gap computing requires non-empty reads")

        if len(self.primer_matches) != 1:
            return "NA (%d primer matches)" % len(self.primer_matches)
        primer_match = self.primer_matches[0]
        if primer_match.strand == -1:
            read_start = max([m.end for m in self.read_matches])
            primer_start = primer_match.end
            gap = primer_start - read_start
        else:
            read_start = min([m.start for m in self.read_matches])
            primer_start = primer_match.start
            gap = read_start - primer_start
        if gap < 0:
            gap += len(self.reference)
        return gap

    @property
    def farthest_reading_span(self):
        """Return the distance between the primer and the furthest match."""
        if len(self.read_matches) == 0:
            return 0

        if len(self.primer_matches) != 1:
            return "NA (%d primer matches)" % len(self.primer_matches)
        primer_match = self.primer_matches[0]
        if primer_match.strand == -1:
            read_end = min([m.start for m in self.read_matches])
            read_start = min([m.start for m in self.read_matches])
            span = read_start - read_end
        else:
            read_end = max([m.end for m in self.read_matches])
            read_start = min([m.start for m in self.read_matches])
            span = read_end - read_start
        if span < 0:
            span += len(self.reference)
        return span

    @property
    def longest_match_size(self):
        if len(self.read_matches) == 0:
            return 0
        return max(len(match) for match in self.read_matches)

    @property
    def total_matches_length(self):
        return sum([len(match) for match in self.read_matches])

    @property
    def primer_start(self):
        if len(self.primer_matches) == 0:
            return None
        if len(self.primer_matches) == 1:
            return self.primer_matches[0].start
        else:
            return [m.start for m in self.primer_matches]

    def matches_as_Biopython_features(self):
        """Return a list of the matches as Biopython features"""
        result = []
        for m in self.read_matches:
            feature = m.to_Biopython_feature()
            if self.primer is not None:
                feature.qualifiers["primer"] = self.primer.name
            result.append(feature)
        for m in self.primer_matches:
            feature = m.to_Biopython_feature()
            if self.primer is not None:
                feature.qualifiers["label"] = self.primer.name
            result.append(feature)
        return result

    def to_SeqRecord(self):
        """Return a Biopython Seqrecord with the matches as annotations.

        The record also features the reference's features.

        """
        record = deepcopy(self.reference)
        record.features += self.matches_as_Biopython_features()
        return record

    def to_genbank(self, filename):
        """Write the Genbank record with the matches as annotations.
        The record also features the reference's features.
        """
        SeqIO.write(self.to_SeqRecord(), filename, "genbank")


class ReadReferenceMatchesSet:
    """All matches between a list of sequencing reads and one reference seq.

    Parameters
    ----------

    read_reference_matches

    linear
      Whether the reference is linear
    """

    def __init__(self, read_reference_matches, linear=True):

        self.read_reference_matches = read_reference_matches
        self.linear = linear
        self.reference = list(read_reference_matches.values())[0].reference
        self.coverage = np.zeros(len(self.reference))
        for matches in read_reference_matches.values():
            for match in matches.read_matches:
                self.coverage[match.start:match.end] += 1

    def rotated(self, n_bases="auto"):
        if n_bases == "auto":
            nz = np.nonzero(np.hstack([self.coverage, self.coverage]))[0]
            if len(nz) > 0:
                i = np.argmax(np.diff(nz))
                x1, x2 = nz[i], nz[i+1]
                n_bases = int((x1 + x2) / 2) % len(self.reference)
            else:
                n_bases = 0
        new_matches = OrderedDict([
            (name, m.rotated(n_bases))
            for name, m in self.read_reference_matches.items()
        ])
        return ReadReferenceMatchesSet(new_matches, linear=self.linear)

    @staticmethod
    def from_reads(reference, reads, perc_identity=98,
                   primer_perc_identity=95, min_match_length=20,
                   max_read_length=1000, linear=True):
        reference_sequence = (reference if isinstance(reference, str) else
                              str(reference.seq))
        reads = deepcopy(reads)
        reads_dict = {read.read_name: read for read in reads}
        seq_length = len(reference_sequence)
        read_reference_matches = OrderedDict([
            (read.read_name, ReadReferenceMatches(
                reference, primer=read.primer, read=read))
            for read in reads
        ])
        if not linear:
            reference_sequence = reference_sequence + reference_sequence

        reads_primer_sequences = {
            read.read_name: read.primer.sequence
            for read in reads
            if (read.primer is not None) and
               (read.primer.sequence is not None)
        }

        if len(reads_primer_sequences):
            primers_blast_record = blast_sequences(
                reads_primer_sequences, subject=reference_sequence,
                perc_identity=primer_perc_identity
            )

            for primer_alignments in primers_blast_record:
                read_name = primer_alignments.query
                name_matches = read_reference_matches[read_name].primer_matches
                read = reads_dict[read_name]
                for aligmnent in primer_alignments.alignments:
                    for hsp in aligmnent.hsps:
                        if hsp.align_length < len(read.primer.sequence):
                            continue
                        if hsp.sbjct_start > seq_length:
                            continue
                        match = SequenceMatch.from_hsp(hsp)
                        name_matches.append(match)

        reads_sequences = {
            read.read_name: read.read_sequence
            for read in reads
            if read.read_sequence is not None
        }
        reads_blast_record = blast_sequences(reads_sequences,
                                             subject=reference_sequence,
                                             perc_identity=perc_identity)

        for reads_alignments in reads_blast_record:

            read_name = reads_alignments.query
            primer_matches = read_reference_matches[read_name].primer_matches

            read = reads_dict[read_name]
            for al in reads_alignments.alignments:
                for hsp in al.hsps:
                    if hsp.align_length < min_match_length:
                        continue
                    start, end = hsp.sbjct_start, hsp.sbjct_end
                    if start > end:
                        start, end = end, start

                    if start > seq_length:
                        continue

                    if len(primer_matches) == 1:
                        # use primer location information to restrict matches
                        m = primer_matches[0]
                        if (m.strand == 1):
                            mstarts = [m.start]
                            if not linear:
                                mstarts.append(m.start-seq_length)
                            if not any([(mstart < start <
                                         mstart + max_read_length)
                                        for mstart in mstarts]):
                                continue
                        else:
                            mstarts = [m.start]
                            if not linear:
                                mstarts.append(m.start + seq_length)
                            if not any([(mstart > end >
                                         mstart - max_read_length)
                                        for mstart in mstarts]):
                                continue

                    match = SequenceMatch.from_hsp(hsp, read=read)

                    read_reference_matches[read_name].read_matches.append(match)

        for sequencing_match in read_reference_matches.values():
            sequencing_match.remove_read_matches_contained_in_others()

        return ReadReferenceMatchesSet(read_reference_matches, linear=linear)

    def plot(self, ax=None, plot_coverage=True,
             plot_reference=False, reference_ax=None,
             figsize="auto", features_filters=(),
             features_properties=None, reference_reads_shares="auto"):
        """Plot the sequencing matches.

        Useful to get a general overview of the sequencing (coverage, mutations
        etc.)

        Parameters
        ----------

        ax
        """

        if plot_reference:
            if features_properties is None:
                def features_properties(f):
                    return {"color": "#f9d277"}

            translator = BiopythonTranslator(
                features_filters=features_filters,
                features_properties=features_properties)
            grecord = translator.translate_record(self.reference)
            if not self.linear:
                grecord.split_overflowing_features_circularly()

        if figsize == "auto":
            figsize = (12, "auto")
        if figsize[1] == "auto":
            sequencing_ax_height = 2 + 0.35 * len(self.read_reference_matches)
            if not plot_reference:
                figure_height = sequencing_ax_height
            else:
                ref_ax, _ = grecord.plot(with_ruler=False,
                                         figure_width=figsize[0])
                ref_fig_height = ref_ax.figure.get_size_inches()[1]
                figure_height = sequencing_ax_height + ref_fig_height
                if reference_reads_shares == "auto":
                    reference_reads_shares = (int(100*ref_fig_height),
                                              int(100*sequencing_ax_height))
                plt.close(ref_ax.figure)
            figsize = (figsize[0], figure_height)
        elif reference_reads_shares == "auto":
            reference_reads_shares = (1, 2)

        if plot_reference:
            if reference_ax is None:
                gs = gridspec.GridSpec(sum(reference_reads_shares), 1)
                fig = plt.figure(figsize=figsize, facecolor="w")
                reference_ax = fig.add_subplot(gs[:reference_reads_shares[0]])
                ax = fig.add_subplot(gs[reference_reads_shares[0]:])

            grecord.plot(reference_ax, with_ruler=False)
            self.plot(ax=ax, plot_coverage=plot_coverage, plot_reference=False)
            ax.set_xlim(reference_ax.get_xlim())
            return ax

        # so the first read in the list gets displayed on top
        read_reference_matches = OrderedDict([
            item for item in list(self.read_reference_matches.items())[::-1]
        ])

        if ax is None:
            fig, ax = plt.subplots(1, figsize=figsize)
        ax.set_xlim(0, len(self.reference))
        ax.set_ylim(0, len(read_reference_matches) + 2)
        ax.set_yticks(range(1, len(read_reference_matches) + 1))
        ax.set_yticklabels([name for name in read_reference_matches])
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.yaxis.set_ticks_position('left')
        ax.xaxis.set_ticks_position('bottom')

        gr_record = GraphicRecord(sequence_length=len(self.reference),
                                  features=[])

        for i, (read_name, matches) in enumerate(read_reference_matches.items()):
            y = i+1
            ax.axhline(y, ls=":", lw=0.5, color="k", zorder=-1000)
            for match in matches.read_matches:
                gr_record.features = [GraphicFeature(start=match.start,
                                                     end=match.end,
                                                     strand=match.strand,
                                                     color="#a3c3f7")]
                gr_record.split_overflowing_features_circularly()
                for feature in gr_record.features:
                    gr_record.plot_feature(ax, feature, y, linewidth=0.2)
            for match in matches.primer_matches:
                feature = GraphicFeature(start=match.start, end=match.end,
                                         strand=match.strand, color="#e85558")
                gr_record.plot_feature(ax, feature, y, linewidth=0.2)

        if plot_coverage:
            coverage = np.zeros(len(self.reference))
            for matches in read_reference_matches.values():
                for match in matches.read_matches:
                    coverage[match.start:match.end] += 1
            ax.fill_between(range(len(self.coverage)), self.coverage,
                            zorder=-2000, alpha=0.2, facecolor="#a3c3f7")
        return ax

    def extract_minimal_cover(self):
        """Return a version of the set with the minimal amount of reads that
        cover the same total region as in this set."""

        coverage_dict = {}
        for primer_name, read_matches in self.read_reference_matches.items():
            if len(read_matches.primer_matches) != 1:
                continue
            primer_match = read_matches.read_matches[0]
            coverage_dict[(primer_match.start, primer_match.end)] = primer_name
        coverages = list(coverage_dict.keys())

        selected = [min(coverages, key=lambda start_end: (start_end[0],
                                                          -start_end[1]))]
        while True:
            overlapping = [
                (start, end) for (start, end) in coverages
                if (start <= selected[-1][1] < end)
            ]
            if overlapping != []:
                new_segment = max(overlapping,
                                  key=lambda start_end: start_end[1])
                selected.append(new_segment)
            else:
                new_components = [
                    (start, end) for (start, end) in coverages
                    if (selected[-1][1] < start)
                ]
                if new_components == []:
                    break
                new_segment = min(new_components,
                                  key=lambda start_end: (start_end[0],
                                                         -start_end[1]))
                selected.append(new_segment)
        selected_primers = set(coverage_dict[s] for s in selected)
        return ReadReferenceMatchesSet(
            {
                k: v
                for k, v in self.read_reference_matches.items()
                if k in selected_primers
            },
            linear=self.linear
        )

    def to_SeqRecord(self):
        """Return a Biopython Seqrecord with the matches as annotations.

        The record also features the reference's features.
        """
        record = deepcopy(self.reference)
        for _, matches in self.read_reference_matches.items():
            record.features += matches.matches_as_Biopython_features()
        return record

    def to_genbank(self, filename):
        """Write the Genbank record with the matches as annotations.
        The record also features the reference's features.
        """
        print (filename)
        record = self.to_SeqRecord()
        record.features = [f for f in record.features if f.location is not None]
        SeqIO.write(record, filename, "genbank")
