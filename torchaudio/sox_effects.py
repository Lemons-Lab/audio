import atexit
from typing import Any, Callable, List, Optional, Tuple, Union

import torch
import torchaudio
from torch import Tensor

from torchaudio._internal import (
    module_utils as _mod_utils,
    misc_ops as _misc_ops,
)

if _mod_utils.is_module_available('torchaudio._torchaudio'):
    from . import _torchaudio


_SOX_INITIALIZED: Optional[bool] = False
# This variable has a micro lifecycle. (False -> True -> None)
# False: Not initialized
# True: Initialized
# None: Already shut down (should not be initialized again.)

_SOX_SUCCESS_CODE = 0
# defined at
# https://fossies.org/dox/sox-14.4.2/sox_8h.html#a8e07e80cebeff3339265d89c387cea93a9ef2b87ec303edfe40751d9a85fadeeb


@_mod_utils.requires_module('torchaudio._torchaudio')
def initialize_sox() -> int:
    """Initialize sox for use with effects chains.

    You only need to call this function once to use SoX effects chains multiple times.
    It is safe to call this function multiple times as long as ``shutdown_sox`` is not yet called.
    Once ``shutdown_sox`` is called, you can no longer use SoX effects and calling this function
    results in `RuntimeError`.

    Note:
        This function is not required for simple loading.

    Returns:
        int: Code corresponding to sox_error_t enum. See
        https://fossies.org/dox/sox-14.4.2/sox_8h.html#a8e07e80cebeff3339265d89c387cea93
    """
    global _SOX_INITIALIZED
    if _SOX_INITIALIZED is None:
        raise RuntimeError('SoX effects chain has been already shut down. Can not initialize again.')
    if not _SOX_INITIALIZED:
        code = _torchaudio.initialize_sox()
        if code == _SOX_SUCCESS_CODE:
            _SOX_INITIALIZED = True
            atexit.register(shutdown_sox)
        return code
    return _SOX_SUCCESS_CODE


@_mod_utils.requires_module("torchaudio._torchaudio")
def shutdown_sox() -> int:
    """Showdown sox for effects chain.

    You do not need to call this function as it will be called automatically
    at the end of program execution, if ``initialize_sox`` was called.

    It is safe to call this function multiple times.

    Returns:
        int: Code corresponding to sox_error_t enum. See
        https://fossies.org/dox/sox-14.4.2/sox_8h.html#a8e07e80cebeff3339265d89c387cea93
    """
    global _SOX_INITIALIZED
    if _SOX_INITIALIZED:
        code = _torchaudio.shutdown_sox()
        if code == _SOX_INITIALIZED:
            _SOX_INITIALIZED = None
        return code
    return _SOX_SUCCESS_CODE


@_mod_utils.requires_module('torchaudio._torchaudio')
def effect_names() -> List[str]:
    """Gets list of valid sox effect names

    Returns: list[str]

    Example
        >>> EFFECT_NAMES = torchaudio.sox_effects.effect_names()
    """
    return _torchaudio.get_effect_names()


@_mod_utils.requires_module('torchaudio._torchaudio')
def SoxEffect():
    r"""Create an object for passing sox effect information between python and c++

    Returns:
        SoxEffect: An object with the following attributes: ename (str) which is the
        name of effect, and eopts (List[str]) which is a list of effect options.
    """
    return _torchaudio.SoxEffect()


class SoxEffectsChain(object):
    r"""SoX effects chain class.

    Args:
        normalization (bool, number, or callable, optional): If boolean `True`, then output is divided by `1 << 31`
            (assumes signed 32-bit audio), and normalizes to `[-1, 1]`. If `number`, then output is divided by that
            number. If `callable`, then the output is passed as a parameter to the given function, then the
            output is divided by the result. (Default: ``True``)
        channels_first (bool, optional): Set channels first or length first in result.  (Default: ``True``)
        out_siginfo (sox_signalinfo_t, optional): a sox_signalinfo_t type, which could be helpful if the
            audio type cannot be automatically determined. (Default: ``None``)
        out_encinfo (sox_encodinginfo_t, optional): a sox_encodinginfo_t type, which could be set if the
            audio type cannot be automatically determined. (Default: ``None``)
        filetype (str, optional): a filetype or extension to be set if sox cannot determine it
            automatically. . (Default: ``'raw'``)

    Returns:
        Tuple[Tensor, int]: An output Tensor of size `[C x L]` or `[L x C]` where L is the number
        of audio frames and C is the number of channels. An integer which is the sample rate of the
        audio (as listed in the metadata of the file)

    Example
        >>> class MyDataset(Dataset):
        >>>     def __init__(self, audiodir_path):
        >>>         self.data = [os.path.join(audiodir_path, fn) for fn in os.listdir(audiodir_path)]
        >>>         self.E = torchaudio.sox_effects.SoxEffectsChain()
        >>>         self.E.append_effect_to_chain("rate", [16000])  # resample to 16000hz
        >>>         self.E.append_effect_to_chain("channels", ["1"])  # mono signal
        >>>     def __getitem__(self, index):
        >>>         fn = self.data[index]
        >>>         self.E.set_input_file(fn)
        >>>         x, sr = self.E.sox_build_flow_effects()
        >>>         return x, sr
        >>>
        >>>     def __len__(self):
        >>>         return len(self.data)
        >>>
        >>> torchaudio.initialize_sox()
        >>> ds = MyDataset(path_to_audio_files)
        >>> for sig, sr in ds:
        >>>   [do something here]
        >>> torchaudio.shutdown_sox()

    """

    EFFECTS_UNIMPLEMENTED = {"spectrogram", "splice", "noiseprof", "fir"}

    def __init__(self,
                 normalization: Union[bool, float, Callable] = True,
                 channels_first: bool = True,
                 out_siginfo: Any = None,
                 out_encinfo: Any = None,
                 filetype: str = "raw") -> None:
        self.input_file: Optional[str] = None
        self.chain: List[str] = []
        self.MAX_EFFECT_OPTS = 20
        self.out_siginfo = out_siginfo
        self.out_encinfo = out_encinfo
        self.filetype = filetype
        self.normalization = normalization
        self.channels_first = channels_first

        # Define in __init__ to avoid calling at import time
        self.EFFECTS_AVAILABLE = set(effect_names())

    def append_effect_to_chain(self,
                               ename: str,
                               eargs: Optional[Union[List[str], str]] = None) -> None:
        r"""Append effect to a sox effects chain.

        Args:
            ename (str): which is the name of effect
            eargs (List[str] or str, optional): which is a list of effect options. (Default: ``None``)
        """
        e = SoxEffect()
        # check if we have a valid effect
        ename = self._check_effect(ename)
        if eargs is None or eargs == []:
            eargs = [""]
        elif not isinstance(eargs, list):
            eargs = [eargs]
        eargs = self._flatten(eargs)
        if len(eargs) > self.MAX_EFFECT_OPTS:
            raise RuntimeError("Number of effect options ({}) is greater than max "
                               "suggested number of options {}.  Increase MAX_EFFECT_OPTS "
                               "or lower the number of effect options".format(len(eargs), self.MAX_EFFECT_OPTS))
        e.ename = ename
        e.eopts = eargs
        self.chain.append(e)

    @_mod_utils.requires_module('torchaudio._torchaudio')
    def sox_build_flow_effects(self,
                               out: Optional[Tensor] = None) -> Tuple[Tensor, int]:
        r"""Build effects chain and flow effects from input file to output tensor

        Args:
            out (Tensor, optional): Where the output will be written to. (Default: ``None``)

        Returns:
            Tuple[Tensor, int]: An output Tensor of size `[C x L]` or `[L x C]` where L is the number
            of audio frames and C is the number of channels. An integer which is the sample rate of the
            audio (as listed in the metadata of the file)
        """
        # initialize output tensor
        if out is not None:
            _misc_ops.check_input(out)
        else:
            out = torch.FloatTensor()
        if not len(self.chain):
            e = SoxEffect()
            e.ename = "no_effects"
            e.eopts = [""]
            self.chain.append(e)

        # print("effect options:", [x.eopts for x in self.chain])

        sr = _torchaudio.build_flow_effects(self.input_file,
                                            out,
                                            self.channels_first,
                                            self.out_siginfo,
                                            self.out_encinfo,
                                            self.filetype,
                                            self.chain,
                                            self.MAX_EFFECT_OPTS)

        _misc_ops.normalize_audio(out, self.normalization)

        return out, sr

    def clear_chain(self) -> None:
        r"""Clear effects chain in python
        """
        self.chain = []

    def set_input_file(self, input_file: str) -> None:
        r"""Set input file for input of chain

        Args:
            input_file (str): The path to the input file.
        """
        self.input_file = input_file

    def _check_effect(self, e: str) -> str:
        if e.lower() in self.EFFECTS_UNIMPLEMENTED:
            raise NotImplementedError("This effect ({}) is not implement in torchaudio".format(e))
        elif e.lower() not in self.EFFECTS_AVAILABLE:
            raise LookupError("Effect name, {}, not valid".format(e.lower()))
        return e.lower()

    # https://stackoverflow.com/questions/12472338/flattening-a-list-recursively
    # convenience function to flatten list recursively
    def _flatten(self, x: list) -> list:
        if x == []:
            return []
        if isinstance(x[0], list):
            return self._flatten(x[:1]) + self._flatten(x[:1])
        return [str(a) for a in x[:1]] + self._flatten(x[1:])
