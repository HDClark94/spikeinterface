import numpy as np

from .base import BaseWidget, to_attr

from .amplitudes import AmplitudesWidget
from .crosscorrelograms import CrossCorrelogramsWidget
from .template_similarity import TemplateSimilarityWidget
from .unit_locations import UnitLocationsWidget
from .unit_templates import UnitTemplatesWidget


from ..core import WaveformExtractor, ChannelSparsity


class SortingSummaryWidget(BaseWidget):
    """
    Plots spike sorting summary

    Parameters
    ----------
    waveform_extractor : WaveformExtractor
        The waveform extractor object.
    sparsity : ChannelSparsity or None
        Optional ChannelSparsity to apply, default None
        If WaveformExtractor is already sparse, the argument is ignored
    max_amplitudes_per_unit : int or None
        Maximum number of spikes per unit for plotting amplitudes,
        by default None (all spikes)
    curation : bool
        If True, manual curation is enabled, by default False
        (sortingview backend)
    unit_table_properties : list or None
        List of properties to be added to the unit table, by default None
        (sortingview backend)
    """

    # possible_backends = {}

    def __init__(
        self,
        waveform_extractor: WaveformExtractor,
        unit_ids=None,
        sparsity=None,
        max_amplitudes_per_unit=None,
        curation=False,
        unit_table_properties=None,
        label_choices=None,
        backend=None,
        **backend_kwargs,
    ):
        self.check_extensions(waveform_extractor, ["correlograms", "spike_amplitudes", "unit_locations", "similarity"])
        we = waveform_extractor
        sorting = we.sorting

        if unit_ids is None:
            unit_ids = sorting.get_unit_ids()

        # use other widgets to generate data (except for similarity)
        # template_plot_data = UnitTemplatesWidget(
        #     we, unit_ids=unit_ids, sparsity=sparsity, hide_unit_selector=True
        # ).plot_data
        # ccg_plot_data = CrossCorrelogramsWidget(we, unit_ids=unit_ids, hide_unit_selector=True).plot_data
        # amps_plot_data = AmplitudesWidget(
        #     we, unit_ids=unit_ids, max_spikes_per_unit=max_amplitudes_per_unit, hide_unit_selector=True
        # ).plot_data
        # locs_plot_data = UnitLocationsWidget(we, unit_ids=unit_ids, hide_unit_selector=True).plot_data
        # sim_plot_data = TemplateSimilarityWidget(we, unit_ids=unit_ids).plot_data

        plot_data = dict(
            waveform_extractor=waveform_extractor,
            unit_ids=unit_ids,
            sparsity=sparsity,
            # templates=template_plot_data,
            # correlograms=ccg_plot_data,
            # amplitudes=amps_plot_data,
            # similarity=sim_plot_data,
            # unit_locations=locs_plot_data,
            unit_table_properties=unit_table_properties,
            curation=curation,
            label_choices=label_choices,
            max_amplitudes_per_unit=max_amplitudes_per_unit,
        )

        BaseWidget.__init__(self, plot_data, backend=backend, **backend_kwargs)

    def plot_sortingview(self, data_plot, **backend_kwargs):
        import sortingview.views as vv
        from .utils_sortingview import generate_unit_table_view, make_serializable, handle_display_and_url

        dp = to_attr(data_plot)
        we = dp.waveform_extractor
        unit_ids = dp.unit_ids
        sparsity = dp.sparsity

        # unit_ids = self.make_serializable(dp.unit_ids)
        unit_ids = make_serializable(dp.unit_ids)

        # backend_kwargs = self.update_backend_kwargs(**backend_kwargs)

        # amplitudes_plotter = AmplitudesPlotter()
        # v_spike_amplitudes = amplitudes_plotter.do_plot(
        #     dp.amplitudes, generate_url=False, display=False, backend="sortingview"
        # )
        # template_plotter = UnitTemplatesPlotter()
        # v_average_waveforms = template_plotter.do_plot(
        #     dp.templates, generate_url=False, display=False, backend="sortingview"
        # )
        # xcorrelograms_plotter = CrossCorrelogramsPlotter()
        # v_cross_correlograms = xcorrelograms_plotter.do_plot(
        #     dp.correlograms, generate_url=False, display=False, backend="sortingview"
        # )
        # unitlocation_plotter = UnitLocationsPlotter()
        # v_unit_locations = unitlocation_plotter.do_plot(
        #     dp.unit_locations, generate_url=False, display=False, backend="sortingview"
        # )

        v_spike_amplitudes = AmplitudesWidget(
            we,
            unit_ids=unit_ids,
            max_spikes_per_unit=dp.max_amplitudes_per_unit,
            hide_unit_selector=True,
            generate_url=False,
            display=False,
            backend="sortingview",
        ).view
        v_average_waveforms = UnitTemplatesWidget(
            we,
            unit_ids=unit_ids,
            sparsity=sparsity,
            hide_unit_selector=True,
            generate_url=False,
            display=False,
            backend="sortingview",
        ).view
        v_cross_correlograms = CrossCorrelogramsWidget(
            we, unit_ids=unit_ids, hide_unit_selector=True, generate_url=False, display=False, backend="sortingview"
        ).view

        v_unit_locations = UnitLocationsWidget(
            we, unit_ids=unit_ids, hide_unit_selector=True, generate_url=False, display=False, backend="sortingview"
        ).view

        w = TemplateSimilarityWidget(
            we, unit_ids=unit_ids, immediate_plot=False, generate_url=False, display=False, backend="sortingview"
        )
        similarity = w.data_plot["similarity"]
        print(similarity.shape)

        # similarity
        similarity_scores = []
        for i1, u1 in enumerate(unit_ids):
            for i2, u2 in enumerate(unit_ids):
                similarity_scores.append(
                    vv.UnitSimilarityScore(unit_id1=u1, unit_id2=u2, similarity=similarity[i1, i2].astype("float32"))
                )

        # unit ids
        v_units_table = generate_unit_table_view(
            dp.waveform_extractor.sorting, dp.unit_table_properties, similarity_scores=similarity_scores
        )

        if dp.curation:
            v_curation = vv.SortingCuration2(label_choices=dp.label_choices)
            v1 = vv.Splitter(direction="vertical", item1=vv.LayoutItem(v_units_table), item2=vv.LayoutItem(v_curation))
        else:
            v1 = v_units_table
        v2 = vv.Splitter(
            direction="horizontal",
            item1=vv.LayoutItem(v_unit_locations, stretch=0.2),
            item2=vv.LayoutItem(
                vv.Splitter(
                    direction="horizontal",
                    item1=vv.LayoutItem(v_average_waveforms),
                    item2=vv.LayoutItem(
                        vv.Splitter(
                            direction="vertical",
                            item1=vv.LayoutItem(v_spike_amplitudes),
                            item2=vv.LayoutItem(v_cross_correlograms),
                        )
                    ),
                )
            ),
        )

        # assemble layout
        # v_summary = vv.Splitter(direction="horizontal", item1=vv.LayoutItem(v1), item2=vv.LayoutItem(v2))
        self.view = vv.Splitter(direction="horizontal", item1=vv.LayoutItem(v1), item2=vv.LayoutItem(v2))

        # self.handle_display_and_url(v_summary, **backend_kwargs)
        # return v_summary

        self.url = handle_display_and_url(self, self.view, **self.backend_kwargs)
