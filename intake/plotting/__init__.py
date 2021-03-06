from __future__ import absolute_import
from functools import partial
from distutils.version import LooseVersion

import param
import holoviews as hv
import pandas as pd

from holoviews.core.spaces import DynamicMap, Callable
from holoviews.core.overlay import NdOverlay
from holoviews.element import (
    Curve, Scatter, Area, Bars, BoxWhisker, Dataset, Distribution,
    Table, HeatMap
)
from holoviews.operation import histogram
from holoviews.streams import Buffer

from ..source.base import DataSource

try:
    from tornado.ioloop import PeriodicCallback
    from tornado import gen
except ImportError:
    gen = None

try:
    import dask.dataframe as dd
except ImportError:
    dd = None

try:
    import bokeh
    if LooseVersion(bokeh.__version__) <= '0.12.14':
        import warnings
        # Ignore NumPy future warnings triggered by bokeh
        warnings.simplefilter(action='ignore', category=FutureWarning)
except:
    pass

class StreamingCallable(Callable):
    """
    StreamingCallable is a DynamicMap callback wrapper which keeps
    a handle to start and stop a dynamic stream.
    """

    periodic = param.Parameter()

    def clone(self, callable=None, **overrides):
        """
        Allows making a copy of the Callable optionally overriding
        the callable and other parameters.
        """
        old = {k: v for k, v in self.get_param_values()
               if k not in ['callable', 'name']}
        params = dict(old, **overrides)
        callable = self.callable if callable is None else callable
        return self.__class__(callable, **params)

    def start(self):
        """
        Start the periodic callback
        """
        if not self.periodic._running:
            self.periodic.start()
        else:
            raise Exception('PeriodicCallback already running.')

    def stop(self):
        """
        Stop the periodic callback
        """
        if self.periodic._running:
            self.periodic.stop()
        else:
            raise Exception('PeriodicCallback not running.')


def streaming(method):
    """
    Decorator to add streaming support to plots.
    """
    def streaming_plot(*args, **kwargs):
        self = args[0]
        if self.streaming:
            cbcallable = StreamingCallable(partial(method, *args, **kwargs),
                                           periodic=self.cb)
            return DynamicMap(cbcallable, streams=[self.stream])
        return method(*args, **kwargs)
    return streaming_plot


class HoloViewsConverter(object):

    def __init__(self, data, kind=None, by=None, width=700,
                 height=300, shared_axes=False, columns=None,
                 grid=False, legend=True, rot=None, title=None,
                 xlim=None, ylim=None, xticks=None, yticks=None,
                 fontsize=None, colormap=None, stacked=False,
                 logx=False, logy=False, loglog=False, hover=True,
                 style_opts={}, plot_opts={}, use_index=False,
                 value_label='value', group_label='Group',
                 colorbar=False, streaming=False, backlog=1000,
                 timeout=1000, persist=False, use_dask=False, **kwds):

        # Validate DataSource
        if not isinstance(data, DataSource):
            raise TypeError('Can only plot intake DataSource types')
        elif data.container != 'dataframe':
            raise NotImplementedError('Plotting interface currently only '
                                      'supports DataSource objects with '
                                      'dataframe container.')
        self.data_source = data
        self.streaming = streaming
        self.use_dask = use_dask
        if streaming:
            self.data = data.read()
            self.stream = Buffer(self.data, length=backlog)
            if gen is None:
                raise ImportError('Streaming support requires tornado.')
            @gen.coroutine
            def f():
                self.stream.send(data.read())
            self.cb = PeriodicCallback(f, timeout)
        elif use_dask and dd is not None:
            ddf = data.to_dask()
            self.data = ddf.persist() if persist else ddf
        else:
            self.data = data.read()

        # High-level options
        self.by = by or []
        self.columns = columns
        self.stacked = stacked
        self.use_index = use_index
        self.kwds = kwds
        self.value_label = value_label
        self.group_label = group_label

        # Process style options
        if 'cmap' in kwds and colormap:
            raise TypeError("Only specify one of `cmap` and `colormap`.")
        elif 'cmap' in kwds:
            cmap = kwds.pop('cmap')
        else:
            cmap = colormap

        self._style_opts = dict(**style_opts)
        if cmap:
            self._style_opts['cmap'] = cmap
        if 'size' in kwds:
            self._style_opts['size'] = kwds.pop('size')
        if 'alpha' in kwds:
            self._style_opts['alpha'] = kwds.pop('alpha')

        # Process plot options
        plot_options = dict(plot_opts)
        plot_options['logx'] = logx or loglog
        plot_options['logy'] = logy or loglog
        plot_options['show_grid'] = grid
        plot_options['shared_axes'] = shared_axes
        plot_options['show_legend'] = legend
        if xticks:
            plot_options['xticks'] = xticks
        if yticks:
            plot_options['yticks'] = yticks
        if width:
            plot_options['width'] = width
        if height:
            plot_options['height'] = height
        if fontsize:
            plot_options['fontsize'] = fontsize
        if colorbar:
            plot_options['colorbar'] = colorbar
        if self.kwds.get('vert', False):
            plot_options['invert_axes'] = True
        if rot:
            if (kind == 'barh' or kwds.get('orientation') == 'horizontal'
                or kwds.get('vert')):
                axis = 'yrotation'
            else:
                axis = 'xrotation'
            plot_options[axis] = rot
        if hover:
            plot_options['tools'] = ['hover']
        self._hover = hover
        self._plot_opts = plot_options

        self._relabel = {'label': title}
        self._dim_ranges = {'x': xlim or (None, None),
                            'y': ylim or (None, None)}
        self._norm_opts = {'framewise': True}

    @streaming
    def table(self, x=None, y=None, data=None):
        allowed = ['width', 'height']
        opts = {k: v for k, v in self._plot_opts.items() if k in allowed}

        data = self.data if data is None else data
        return Table(data, self.columns, []).opts(plot=opts)

    def __call__(self, kind, x, y):
        return getattr(self, kind)(x, y)

    def single_chart(self, element, x, y, data=None):
        opts = {element.__name__: dict(plot=self._plot_opts, norm=self._norm_opts,
                                       style=self._style_opts)}
        ranges = {y: self._dim_ranges['y']}
        if x:
            ranges[x] = self._dim_ranges['x']

        data = self.data if data is None else data
        ys = [y]
        if 'c' in self.kwds and self.kwds['c'] in data.columns:
            ys += [self.kwds['c']]

        if self.by:
            chart = Dataset(data, [self.by, x], ys).to(element, x, ys, self.by).overlay()
        else:
            chart = element(data, x, ys)
        return chart.redim.range(**ranges).relabel(**self._relabel).opts(opts)

    def chart(self, element, x, y, data=None):
        "Helper method for simple x vs. y charts"
        if x and y:
            return self.single_chart(element, x, y, data)

        # Note: Loading dask dataframe into memory due to rename bug
        data = (self.data if data is None else data)
        if self.use_dask: data = data.compute()
        opts = dict(plot=dict(self._plot_opts, labelled=['x']),
                    norm=self._norm_opts, style=self._style_opts)

        if self.use_index or x:
            if self.use_index is not None and isinstance(self.use_index, bool):
                x = x or data.index.name or 'index'
            else:
                x = self.use_index
            columns = self.columns or data.columns
            charts = {}
            for c in columns:
                chart = element(data, x, c).redim(**{c: self.value_label})
                ranges = {x: self._dim_ranges['x'], c: self._dim_ranges['y']}
                charts[c] = (chart.relabel(**self._relabel)
                             .redim.range(**ranges).opts(**opts))
            return NdOverlay(charts)
        else:
            raise ValueError('Could not determine what to plot. Expected '
                             'either x and y parameters to be declared '
                             'or use_index to be enabled.')

    @streaming
    def line(self, x, y, data=None):
        return self.chart(Curve, x, y, data)

    @streaming
    def scatter(self, x, y, data=None):
        scatter = self.chart(Scatter, x, y, data)
        if 'c' in self.kwds:
            color_opts = {'Scatter': {'colorbar': self.kwds.get('colorbar', False),
                                      'color_index': self.kwds['c']}}
            return scatter.opts(plot=color_opts)
        return scatter

    @streaming
    def area(self, x, y, data=None):
        areas = self.chart(Area, x, y, data)
        if self.stacked:
            areas = areas.map(Area.stack, NdOverlay)
        return areas

    def _category_plot(self, element, data=None):
        """
        Helper method to generate element from indexed dataframe.
        """
        data = self.data if data is None else data
        if isinstance(self.use_index, bool):
            index = data.index.name or 'index'
        else:
            index = self.use_index

        kdims = [index, self.group_label]
        id_vars = [index]
        invert = not self.kwds.get('vert', True)
        opts = {'plot': dict(self._plot_opts, labelled=[]),
                'norm': self._norm_opts}
        ranges = {self.value_label: self._dim_ranges['y']}

        if self.columns:
            data = data[self.columns+id_vars]
        if dd and isinstance(data, dd.DataFrame):
            data = data.compute()
        df = pd.melt(data, id_vars=id_vars, var_name=self.group_label,
                     value_name=self.value_label)
        return (element(df, kdims, self.value_label).redim.range(**ranges)
                .relabel(**self._relabel).opts(**opts))


    def _stats_plot(self, element, y, data=None):
        """
        Helper method to generate element from indexed dataframe.
        """
        data = self.data if data is None else data

        opts = {'plot': dict(self._plot_opts, labelled=[]),
                'norm': self._norm_opts, 'style': self._style_opts}
        if y:
            ranges = {y: self._dim_ranges['y']}
            kdims = [self.by] if self.by else []
            return (element(data, kdims, y).redim.range(**ranges)
                .relabel(**self._relabel).opts(**opts))

        kdims = [self.group_label]
        ranges = {self.value_label: self._dim_ranges['y']}
        if self.columns:
            data = data[self.columns]
        if dd and isinstance(data, dd.DataFrame):
            data = data.compute()
        df = pd.melt(data, var_name=self.group_label, value_name=self.value_label)
        return (element(df, kdims, self.value_label).redim.range(**ranges)
                .relabel(**self._relabel).opts(**opts))


    @streaming
    def bar(self, x, y, data=None):
        if x and y:
            return self.single_chart(Bars, x, y, data)
        elif self.use_index:
            stack_index = 1 if self.stacked else None
            opts = {'Bars': {'stack_index': stack_index}}
            return self._category_plot(Bars, data).opts(plot=opts)
        else:
            raise ValueError('Could not determine what to plot. Expected '
                             'either x and y parameters to be declared '
                             'or use_index to be enabled.')

    @streaming
    def barh(self, x, y, data=None):
        return self.bar(x, y, data).opts(plot={'Bars': dict(invert_axes=True)})


    @streaming
    def box(self, x, y, data=None):
        return self._stats_plot(BoxWhisker, y, data)

    @streaming
    def violin(self, x, y, data=None):
        try:
            from holoviews.element import Violin
        except ImportError:
            raise ImportError('Violin plot requires HoloViews version >=1.10')
        return self._stats_plot(Violin, y, data)

    @streaming
    def hist(self, x, y, data=None):
        plot_opts = dict(self._plot_opts)
        invert = self.kwds.get('orientation', False) == 'horizontal'
        opts = dict(plot=dict(plot_opts, labelled=['x'], invert_axes=invert),
                    style=self._style_opts, norm=self._norm_opts)
        hist_opts = {'num_bins': self.kwds.get('bins', 10),
                     'bin_range': self.kwds.get('bin_range', None),
                     'normed': self.kwds.get('normed', False)}

        data = self.data if data is None else data
        ds = Dataset(data)
        if y and self.by:
            return histogram(ds.to(Dataset, [], y, self.by), **hist_opts).\
                overlay().opts({'Histogram': opts})
        elif y:
            return histogram(ds, dimension=y, **hist_opts).\
                opts({'Histogram': opts})

        hists = {}
        columns = self.columns or data.columns
        for col in columns:
            hist = histogram(ds, dimension=col, **hist_opts)
            ranges = {hist.vdims[0].name: self._dim_ranges['y']}
            hists[col] = (hist.redim.range(**ranges)
                          .relabel(**self._relabel).opts(**opts))
        return NdOverlay(hists)

    @streaming
    def kde(self, x, y, data=None):
        data = self.data if data is None else data
        plot_opts = dict(self._plot_opts)
        invert = self.kwds.get('orientation', False) == 'horizontal'
        opts = dict(plot=dict(plot_opts, invert_axes=invert),
                    style=self._style_opts, norm=self._norm_opts)
        opts = {'Distribution': opts, 'Area': opts,
                'NdOverlay': {'plot': dict(plot_opts, legend_limit=0)}}

        if y and self.by:
            ds = Dataset(data)
            return ds.to(Distribution, y, [], self.by).overlay().opts(opts)
        elif y:
            return Distribution(data, y, []).opts(opts)

        if self.columns:
            data = data[self.columns]
        df = pd.melt(data, var_name=self.group_label, value_name=self.value_label)
        ds = Dataset(df)
        if len(df):
            overlay = ds.to(Distribution, self.value_label).overlay()
        else:
            vdim = self.value_label + ' Density'
            overlay = NdOverlay({0: Area([], self.value_label, vdim)},
                                [self.group_label])
        return overlay.relabel(**self._relabel).opts(opts)

    @streaming
    def heatmap(self, x, y, data=None):
        data = data or self.data
        if not x: x = data.columns[0]
        if not y: y = data.columns[1]
        z = self.kwds.get('C', data.columns[2])


        opts = dict(plot=self._plot_opts, norm=self._norm_opts, style=self._style_opts)
        hmap = HeatMap(data, [x, y], z).opts(**opts)
        if 'reduce_function' in self.kwds:
            return hmap.aggregate(function=self.kwds['reduce_function'])
        return hmap



class HoloViewsDataSourcePlot(object):

    def __init__(self, data):
        self._data = data

    def __call__(self, x=None, y=None, kind='line', backlog=1000,
             width=700, height=300, title=None, grid=False,
             legend=True, logx=False, logy=False, loglog=False,
             xticks=None, yticks=None, xlim=None, ylim=None, rot=None,
             fontsize=None, colormap=None, hover=True, **kwds):
        converter = HoloViewsConverter(
            self._data, width=width, height=height, backlog=backlog,
            title=title, grid=grid, legend=legend, logx=logx,
            logy=logy, loglog=loglog, xticks=xticks, yticks=yticks,
            xlim=xlim, ylim=ylim, rot=rot, fontsize=fontsize,
            colormap=colormap, hover=hover, **kwds
        )
        return converter(kind, x, y)

    def line(self, x=None, y=None, **kwds):
        """
        Line plot

        Parameters
        ----------
        x, y : label or position, optional
            Coordinates for each point.
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(x, y, kind='line', **kwds)

    def scatter(self, x=None, y=None, **kwds):
        """
        Scatter plot

        Parameters
        ----------
        x, y : label or position, optional
            Coordinates for each point.
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(x, y, kind='scatter', **kwds)

    def area(self, x=None, y=None, **kwds):
        """
        Area plot

        Parameters
        ----------
        x, y : label or position, optional
            Coordinates for each point.
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(x, y, kind='area', **kwds)

    def heatmap(self, x=None, y=None, z=None, **kwds):
        """
        HeatMap plot

        Parameters
        ----------
        x, y, z : label or position, optional
            Coordinates for each point.
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(x, y, kind='heatmap', z=z, **kwds)

    def bar(self, x=None, y=None, **kwds):
        """
        Bars plot

        Parameters
        ----------
        x, y : label or position, optional
            Coordinates for each point.
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(x, y, kind='bar', **kwds)

    def barh(self, x=None, y=None, **kwds):
        """
        Horizontal bar plot

        Parameters
        ----------
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(x, y, kind='barh', **kwds)

    def box(self, y=None, **kwds):
        """
        Boxplot

        Parameters
        ----------
        by : string or sequence
            Column in the DataFrame to group by.
        kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(kind='box', x=None, y=y, **dict(kwds, hover=False))

    def violin(self, y=None, **kwds):
        """
        Boxplot

        Parameters
        ----------
        by : string or sequence
            Column in the DataFrame to group by.
        kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(kind='violin', x=None, y=y, **dict(kwds, hover=False))

    def hist(self, y=None, **kwds):
        """
        Histogram

        Parameters
        ----------
        by : string or sequence
            Column in the DataFrame to group by.
        kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(kind='hist', x=None, y=y, **kwds)

    def kde(self, y=None, **kwds):
        """
        KDE

        Parameters
        ----------
        by : string or sequence
            Column in the DataFrame to group by.
        kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(kind='kde', x=None, y=y, **kwds)

    def table(self, columns=None, **kwds):
        """
        Table

        Parameters
        ----------
        **kwds : optional
            Keyword arguments to pass on to
            :py:meth:`intake.source.base.DataSource.plot`.
        Returns
        -------
        Element : Element or NdOverlay of Elements
        """
        return self(kind='table', **dict(kwds, columns=columns))
