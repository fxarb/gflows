import numpy as np
import ctypes
from math import tau
from numba import vectorize, njit
from .logging_config import setup_logging

logger = setup_logging()
from numba.types import float64, UniTuple, string
from numba.extending import get_cython_function_address


# The address of the Cython erf function.
addr = get_cython_function_address("scipy.special.cython_special", "__pyx_fuse_1erf")
# The function type for the erf function.
functype = ctypes.CFUNCTYPE(ctypes.c_double, ctypes.c_double)
# The erf function.
erf_fn = functype(addr)


@vectorize([float64(float64)])
def vec_erf(x):
    """
    A vectorized version of the erf function.

    :param x: The input value.
    :return: The value of the erf function.
    """
    return erf_fn(x)


@njit(float64[:, :](float64[:, :]))
def erf_njit(x):
    """
    A Numba-jitted version of the erf function.

    :param x: The input value.
    :return: The value of the erf function.
    """
    return vec_erf(x)


# Probability density function for a normal distribution
@njit(float64[:, :](float64[:, :], float64, float64))
def norm_pdf(x, mu, sigma):
    """
    The probability density function for a normal distribution.

    :param x: The input value.
    :param mu: The mean of the distribution.
    :param sigma: The standard deviation of the distribution.
    :return: The value of the probability density function.
    """
    variance = sigma**2.0
    return np.exp((x - mu) ** 2.0 / (-2.0 * variance)) / np.sqrt(tau * variance)


# Cumulative distribution function for a normal distribution
@njit(float64[:, :](float64[:, :], float64, float64))
def norm_cdf(x, mu, sigma):
    """
    The cumulative distribution function for a normal distribution.

    :param x: The input value.
    :param mu: The mean of the distribution.
    :param sigma: The standard deviation of the distribution.
    :return: The value of the cumulative distribution function.
    """
    return 0.5 * (1.0 + erf_njit((x - mu) / (sigma * np.sqrt(2.0))))


# S is spot price, K is strike price, vol is implied volatility
# T is time to expiration, r is risk-free rate, q is dividend yield
@njit(
    UniTuple(float64[:, :], 3)(
        float64[:, :], float64[:], float64[:], float64[:], float64, float64
    )
)
def calc_dp_cdf_pdf(S, K, vol, T, r, q):
    """
    Calculates the d+, cdf(d+), and pdf(d+) values for the Black-Scholes formula.

    :param S: The spot price.
    :param K: The strike price.
    :param vol: The implied volatility.
    :param T: The time to expiration.
    :param r: The risk-free rate.
    :param q: The dividend yield.
    :return: A tuple containing the d+, cdf(d+), and pdf(d+) values.
    """
    dp = (np.log(S / K) + (r - q + 0.5 * vol**2) * T) / (vol * np.sqrt(T))
    cdf_dp = norm_cdf(dp, 0.0, 1.0)
    pdf_dp = norm_pdf(dp, 0.0, 1.0)
    return dp, cdf_dp, pdf_dp


# Black-Scholes Pricing Formula


@njit(
    float64[:, :](float64[:, :], float64[:], float64, string, float64[:], float64[:, :])
)
def calc_delta_ex(S, T, q, opt_type, OI, cdf_dp):
    """
    Calculates the delta exposure.

    :param S: The spot price.
    :param T: The time to expiration.
    :param q: The dividend yield.
    :param opt_type: The option type.
    :param OI: The open interest.
    :param cdf_dp: The cdf(d+) value.
    :return: The delta exposure.
    """
    if opt_type == "call":
        delta = np.exp(-q * T) * cdf_dp
    else:
        delta = -np.exp(-q * T) * (1 - cdf_dp)
    # change in option price per one percent move in underlying
    return delta * OI * S * 0.01


@njit(
    float64[:, :](
        float64[:, :], float64[:], float64[:], float64, float64[:], float64[:, :]
    )
)
def calc_gamma_ex(S, vol, T, q, OI, pdf_dp):
    """
    Calculates the gamma exposure.

    :param S: The spot price.
    :param vol: The implied volatility.
    :param T: The time to expiration.
    :param q: The dividend yield.
    :param OI: The open interest.
    :param pdf_dp: The pdf(d+) value.
    :return: The gamma exposure.
    """
    gamma = np.exp(-q * T) * pdf_dp / (S * vol * np.sqrt(T))
    # change in delta per one percent move in underlying
    return gamma * OI * S * S * 0.01  # Gamma is same formula for calls and puts


@njit(
    float64[:, :](
        float64[:, :],
        float64[:],
        float64[:],
        float64,
        float64[:],
        float64[:, :],
        float64[:, :],
    )
)
def calc_vanna_ex(S, vol, T, q, OI, dp, pdf_dp):
    """
    Calculates the vanna exposure.

    :param S: The spot price.
    :param vol: The implied volatility.
    :param T: The time to expiration.
    :param q: The dividend yield.
    :param OI: The open interest.
    :param dp: The d+ value.
    :param pdf_dp: The pdf(d+) value.
    :return: The vanna exposure.
    """
    dm = dp - vol * np.sqrt(T)
    vanna = -np.exp(-q * T) * pdf_dp * (dm / vol)
    # change in delta per one percent move in IV
    # or change in vega per one percent move in underlying
    return vanna * OI * S * 0.01  # Vanna is same formula for calls and puts


@njit(
    float64[:, :](
        float64[:, :],
        float64[:],
        float64[:],
        float64,
        float64,
        string,
        float64[:],
        float64[:, :],
        float64[:, :],
        float64[:, :],
    )
)
def calc_charm_ex(S, vol, T, r, q, opt_type, OI, dp, cdf_dp, pdf_dp):
    """
    Calculates the charm exposure.

    :param S: The spot price.
    :param vol: The implied volatility.
    :param T: The time to expiration.
    :param r: The risk-free rate.
    :param q: The dividend yield.
    :param opt_type: The option type.
    :param OI: The open interest.
    :param dp: The d+ value.
    :param cdf_dp: The cdf(d+) value.
    :param pdf_dp: The pdf(d+) value.
    :return: The charm exposure.
    """
    dm = dp - vol * np.sqrt(T)
    if opt_type == "call":
        charm = (q * np.exp(-q * T) * cdf_dp) - np.exp(-q * T) * pdf_dp * (
            2 * (r - q) * T - dm * vol * np.sqrt(T)
        ) / (2 * T * vol * np.sqrt(T))
    else:
        charm = (-q * np.exp(-q * T) * (1 - cdf_dp)) - np.exp(-q * T) * pdf_dp * (
            2 * (r - q) * T - dm * vol * np.sqrt(T)
        ) / (2 * T * vol * np.sqrt(T))
    # change in delta per day until expiration
    return charm * OI * S * (1.0 / 365.0)
