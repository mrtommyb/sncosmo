# Licensed under a 3-clause BSD style license - see LICENSE.rst

import abc
from copy import deepcopy

import numpy as np
from astropy.utils import OrderedDict
from astropy.utils.misc import lazyproperty
import astropy.units as u
from astropy import cosmology
import astropy.constants as const

from . import registry

__all__ = ['get_bandpass', 'get_magsystem', 'Bandpass', 'Spectrum',
           'MagSystem', 'SpectralMagSystem', 'ABMagSystem']

def get_bandpass(name):
    """Get a Bandpass from the registry by name."""
    return registry.retrieve(Bandpass, name)

def get_magsystem(name):
    """Get a MagSystem from the registery by name."""
    return registry.retrieve(MagSystem, name)


class Bandpass(object):
    """Transmission as a function of spectral dispersion.

    Parameters
    ----------
    disp : list_like
        Dispersion. Monotonically increasing values.
    trans : list_like
        Transmission fraction.
    dunit : `~astropy.units.Unit` or str
        Dispersion unit. Default is Angstroms.
    name : str
        Identifier.
    """

    def __init__(self, disp, trans, dunit=u.AA, name=None):
        
        self._disp = np.asarray(disp) 
        self._trans = np.asarray(trans)
        if self._disp.shape != self._trans.shape:
            raise ValueError('shape of wavelengths and '
                             'transmission must match')
        if self._disp.ndim != 1:
            raise ValueError('only 1-d arrays supported')
        self._dunit = dunit
        self._name = name
        
    @property
    def name(self):
        """Name of bandpass."""
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def disp(self):
        """Dispersion values (always monotonically increasing)."""
        return self._disp

    @property
    def trans(self):
        """Transmission values corresponding to dispersion values."""
        return self._trans

    @property
    def dunit(self):
        """Dispersion unit"""
        return self._dunit

    @lazyproperty
    def disp_eff(self):
        """Effective dispersion value of bandpass."""
        if self._dunit.physical_type != u.m.physical_type:
            raise ValueError('disp_eff only defined for wavelength dispersion')
        weights = self._trans * np.gradient(self._disp) 
        return np.sum(self._disp * weights) / np.sum(weights)

    def to_unit(self, new_unit):
        """Return a new bandpass instance with the requested dispersion units.
        
        A new instance is necessary because the dispersion and transmission
        values may need to be reordered to ensure that they remain
        monotonically increasing.

        If the requested units are the same as the current units, self is
        returned.
        """

        if new_unit == self._dunit:
            return self

        d = self._dunit.to(new_unit, self._disp, u.spectral())
        t = self._trans
        if d[0] > d[-1]:
            d = np.flipud(d)
            t = np.flipud(t)
        return Bandpass(d, t, dunit=new_unit, name=self._name)

    def __repr__(self):
        name = ''
        if self._name is not None:
            name = ' {0!r:s}'.format(self._name)
        return "<Bandpass{0:s} at 0x{1:x}>".format(name, id(self))


class Spectrum(object):
    """A spectrum, representing dispersion and spectral density values.

    Parameters
    ----------
    disp : list_like
        Dispersion values.
    flux : list_like
        Spectral flux density values.
    error : list_like, optional
        1 standard deviation uncertainty on flux density values.
    dunit : `~astropy.units.Unit`
        Units 
    units : `~astropy.units.BaseUnit`
        For now, only units with flux density in energy (not photon counts).
    z : float, optional
        Redshift of spectrum (default is `None`)
    dist : float, optional
        Luminosity distance in Mpc, used to adjust flux upon redshifting.
        The default is ``None``.
    meta : OrderedDict, optional
        Metadata.
    """

    def __init__(self, disp, flux, error=None,
                 unit=(u.erg / u.s / u.cm**2 / u.AA), dunit=u.AA,
                 z=None, dist=None, meta=None):
        
        self._disp = np.asarray(disp)
        self._flux = np.asarray(flux)
        self._dunit = dunit
        self._unit = unit
        self._z = z
        self._dist = dist

        if error is not None:
            self._error = np.asarray(error)
            if self._disp.shape != self._error.shape:
                raise ValueError('shape of wavelength and variance must match')
        else:
            self._error = None

        if meta is None:
            self.meta = OrderedDict()
        else:
            self.meta = deepcopy(meta)

        if self._disp.shape != self._flux.shape:
            raise ValueError('shape of wavelength and flux must match')
        if self._disp.ndim != 1:
            raise ValueError('only 1-d arrays supported')


    @property
    def disp(self):
        """Dispersion values."""
        return self._disp
        
    @property
    def flux(self):
        """Spectral flux density values"""
        return self._flux

    @property
    def error(self):
        """Uncertainty on flux density."""
        return self._error

    @property
    def dunit(self):
        """Units of dispersion."""
        return self._dunit
        
    @property
    def unit(self):
        """Units of flux density."""
        return self._unit

    @property
    def z(self):
        """Redshift of spectrum."""
        return self._z

    @z.setter
    def z(self, value):
        self._z = value

    @property
    def dist(self):
        """Distance to object in Mpc."""
        return self._dist

    @dist.setter
    def dist(self, value):
        self._dist = value


    def bandflux(self, band):
        """Perform synthentic photometry in a given bandpass.
      
        The bandpass transmission is interpolated onto the dispersion grid
        of the spectrum. The result is a weighted sum of the spectral flux
        density values (weighted by transmission values).
        
        Parameters
        ----------
        band : Bandpass object or name of registered bandpass.

        Returns
        -------
        bandflux : float
            Total flux in ph/s/cm^2. If part of bandpass falls
            outside the spectrum, `None` is returned instead.
        bandfluxerr : float
            Error on flux. Only returned if the `error` attribute is not
            `None`.
        """

        band = get_bandpass(band)
        band = band.to_unit(self._dunit)

        if (band.disp[0] < self._disp[0] or
            band.disp[-1] > self._disp[-1]):
            return None

        idx = ((self._disp > band.disp[0]) & 
               (self._disp < band.disp[-1]))
        d = self._disp[idx]
        f = self._flux[idx]

        #TODO: use spectral density equivalencies once they can do photons.
        # first convert to ergs / s /cm^2 / (dispersion unit)
        target_unit = u.erg / u.s / u.cm**2 / self._dunit
        if self._unit != target_unit:
            f = self._unit.to(target_unit, f, 
                              u.spectral_density(self._dunit, d))

        # Then convert ergs to photons: photons = Energy / (h * nu)
        f = f / const.h.cgs.value / self._dunit.to(u.Hz, d, u.spectral())

        trans = np.interp(d, band.disp, band.trans)
        binw = np.gradient(d)
        ftot = np.sum(f * trans * binw)

        if self._error is None:
            return ftot

        else:
            e = self._error[idx]

            # Do the same conversion as above
            if self._unit != target_unit:
                e = self._unit.to(target_unit, e, 
                                  u.spectral_density(self._dunit, d))
            e = e / const.h.cgs.value / self._dunit.to(u.Hz, d, u.spectral())
            etot = np.sqrt(np.sum((e * binw) ** 2 * trans))
            return ftot, etot



    def redshifted_to(self, z, adjust_flux=False, dist=None, cosmo=None):
        """Return a new Spectrum object at a new redshift.

        The current redshift must be defined (self.z cannot be `None`).
        A factor of (1 + z) / (1 + self.z) is applied to the wavelength. 
        The inverse factor is applied to the flux so that the bolometric
        flux (e.g., erg/s/cm^2) remains constant.
        
        .. note:: Currently this only works for units in ergs.

        Parameters
        ----------
        z : float
            Target redshift.
        adjust_flux : bool, optional
            If True, the bolometric flux is adjusted by
            ``F_out = F_in * (D_in / D_out) ** 2``, where ``D_in`` and
            ``D_out`` are current and target luminosity distances,
            respectively. ``D_in`` is self.dist. If self.dist is ``None``,
            the distance is calculated from the current redshift and
            given cosmology.
        dist : float, optional
            Output distance in Mpc. Used to adjust bolometric flux if
            ``adjust_flux`` is ``True``. Default is ``None`` which means
            that the distance is calculated from the redshift and the
            cosmology.
        cosmo : `~astropy.cosmology.Cosmology` instance, optional
            The cosmology used to estimate distances if dist is not given.
            Default is ``None``, which results in using the default
            cosmology.

        Returns
        -------
        spec : Spectrum object
            A new spectrum object at redshift z.
        """

        if self._z is None:
            raise ValueError('Must set current redshift in order to redshift'
                             ' spectrum')

        if self._dunit.physical_type == u.m.physical_type:
            factor = (1. + z) / (1. + self._z)
        elif self._dunit.physical_type == u.Hz.physical_type:
            factor = (1. + self._z) / (1. + z)
        else:
            raise ValueError('dispersion must be in wavelength or frequency')

        d = self._disp * factor
        f = self._flux / factor
        if self._error is not None:
            e = self._error / factor
        else:
            e = None

        if adjust_flux:
            if self._dist is None and self._z == 0.:
                raise ValueError("When current redshift is 0 and adjust_flux "
                                 "is requested, current distance must be "
                                 "defined")
            if dist is None and z == 0.:
                raise ValueError("When redshift is 0 and adjust_flux "
                                 "is requested, dist must be defined")
            if cosmo is None:
                cosmo = cosmology.get_current()

            if self._dist is None:
                dist_in = cosmo.luminosity_distance(self._z)
            else:
                dist_in = self._dist

            if dist is None: dist = cosmo.luminosity_distance(z)

            if dist_in <= 0. or dist <= 0.:
                raise ValueError("Distances must be greater than 0.")

            # Adjust the flux
            factor = (dist_in / dist) ** 2
            f *= factor
            if e is not None: e *= factor

        return Spectrum(d, f, error=e, z=z, dist=dist, meta=self.meta,
                        unit=self._unit, dunit=self._dunit)


class MagSystem(object):
    """An abstract base class for magnitude systems."""

    __metaclass__ = abc.ABCMeta

    def __init__(self, refmags=None, name=None):
        self._zpbandflux = {}
        self._name = name
        self._refmags = refmags

    @abc.abstractmethod
    def _refspectrum_bandflux(self, band):
        """Flux of the fundamental spectrophotometric standard."""
        pass

    @property
    def name(self):
        """Name of magnitude system."""
        return self._name

    @name.setter
    def name(self, value):
        self._name = value


    @property
    def refmags(self):
        """Dictionary of magnitude refmags for particular bands."""
        return copy.copy(self._refmags)

    @refmags.setter
    def refmags(self, new_refmags):
        if new_refmags is None:
            self._refmags = None
            return

        if not isinstance(new_refmags, dict):
            raise ValueError("refmags must be dict-like or None")

        self._refmags = OrderedDict()
        for band, offset in new_refmags:
            self._refmags[get_bandpass(band)] = offset

    def zpbandflux(self, band):
        """Flux of an object with magnitude zero in the given bandpass.

        Parameters
        ----------
        bandpass : `~sncosmo.spectral.Bandpass` or str

        Returns
        -------
        bandflux : float
            Flux in photons / s / cm^2.
        """

        band = get_bandpass(band)
        try:
            return self._zpbandflux[band]
        except KeyError:
            pass

        bandflux = self._refspectrum_bandflux(band)
        if self._refmags is not None:
            if band not in self._refmags:
                raise Exception('Band refmags not defined')
            bandflux *= 10 ** (0.4 * self._refmags[band])
    
        self._zpbandflux[band] = bandflux
        return bandflux

    def band_flux_to_mag(self, flux, band):
        """Convert flux (photons / s / cm^2) to magnitude."""
        return -2.5 * math.log10(flux / self.zpbandflux(band))

    def band_mag_to_flux(self, mag, band):
        """Convert magnitude to flux in photons / s / cm^2"""
        return self.zpbandflux(band) * 10.**(-0.4 * mag)


class SpectralMagSystem(MagSystem):
    """A magnitude system defined by a fundamental spectrophotometric
    standard.

    Parameters
    ----------
    refspectrum : `sncosmo.Spectrum`
        The spectrum of the fundamental spectrophotometric standard.
    refmags : dict, optional
        The magnitudes (in this magnitude system) of the fundamental
        spectrophotometric standard in the given bandpasses. If `None`
        (the default), it is assumed that the fundamental standard has
        magnitude 0 in all bandpasses. Dictionary keys can either be
        `sncosmo.Bandpass` objects or string identifiers
        thereof. Dictionary values are floats.
    """

    def __init__(self, refspectrum, refmags=None, name=None):
        super(SpectralMagSystem, self).__init__(refmags, name)
        self._refspectrum = refspectrum

    def _refspectrum_bandflux(self, band):
        return self._refspectrum.bandflux(band)


class ABMagSystem(MagSystem):
    """Magnitude system where a source with F_nu = 3631 Jansky at all
    frequencies has magnitude 0 in all bands."""
    
    def _refspectrum_bandflux(self, band):
        b = band.to_unit(u.Hz)

        # AB spectrum is 3631 x 10^{-23} erg/s/cm^2/Hz
        # Get spectral values in photons/cm^2/s/Hz at bandpass dispersions
        # by dividing by (h \nu).
        f = 3631.e-23 / const.h.cgs.value / b.disp

        binw = np.gradient(b.disp)
        return np.sum(f * b.trans * binw)


#    Examples
#    --------
#    >>> chft = LocalMagSystem(bd17spectrum,
#    ...                       {'megacamu': 9.7688, 'megacamg': 9.6906,
#    ...                        'megacamr': 9.2183, 'megacami': 8.9142})
#    >>> chft.zp_cflux('megacamu') # flux an object 9.7688 mag brighter than bd17
#    >>> chft.zp_cflux('desg') # Exception
#    """

