#!/usr/bin/env python
# coding: utf-8

import numpy as np
from autograd import jacobian
import autograd.numpy as anp  # Thinly-wrapped numpy
from .pyerrors import derived_observable, CObs, Obs


# This code block is directly taken from the current master branch of autograd and remains
# only until the new version is released on PyPi
from functools import partial
from autograd.extend import defvjp


def derived_array(func, data, **kwargs):
    """Construct a derived Obs according to func(data, **kwargs) using automatic differentiation.

    Parameters
    ----------
    func -- arbitrary function of the form func(data, **kwargs). For the
            automatic differentiation to work, all numpy functions have to have
            the autograd wrapper (use 'import autograd.numpy as anp').
    data -- list of Obs, e.g. [obs1, obs2, obs3].

    Keyword arguments
    -----------------
    num_grad -- if True, numerical derivatives are used instead of autograd
                (default False). To control the numerical differentiation the
                kwargs of numdifftools.step_generators.MaxStepGenerator
                can be used.
    man_grad -- manually supply a list or an array which contains the jacobian
                of func. Use cautiously, supplying the wrong derivative will
                not be intercepted.

    Notes
    -----
    For simple mathematical operations it can be practical to use anonymous
    functions. For the ratio of two observables one can e.g. use

    new_obs = derived_observable(lambda x: x[0] / x[1], [obs1, obs2])
    """

    data = np.asarray(data)
    raveled_data = data.ravel()

    # Workaround for matrix operations containing non Obs data
    for i_data in raveled_data:
        if isinstance(i_data, Obs):
            first_name = i_data.names[0]
            first_shape = i_data.shape[first_name]
            break

    for i in range(len(raveled_data)):
        if isinstance(raveled_data[i], (int, float)):
            raveled_data[i] = Obs([raveled_data[i] + np.zeros(first_shape)], [first_name])

    n_obs = len(raveled_data)
    new_names = sorted(set([y for x in [o.names for o in raveled_data] for y in x]))

    new_shape = {}
    for i_data in raveled_data:
        for name in new_names:
            tmp = i_data.shape.get(name)
            if tmp is not None:
                if new_shape.get(name) is None:
                    new_shape[name] = tmp
                else:
                    if new_shape[name] != tmp:
                        raise Exception('Shapes of ensemble', name, 'do not match.')
    if data.ndim == 1:
        values = np.array([o.value for o in data])
    else:
        values = np.vectorize(lambda x: x.value)(data)

    new_values = func(values, **kwargs)

    new_r_values = {}
    for name in new_names:
        tmp_values = np.zeros(n_obs)
        for i, item in enumerate(raveled_data):
            tmp = item.r_values.get(name)
            if tmp is None:
                tmp = item.value
            tmp_values[i] = tmp
        tmp_values = np.array(tmp_values).reshape(data.shape)
        new_r_values[name] = func(tmp_values, **kwargs)

    if 'man_grad' in kwargs:
        deriv = np.asarray(kwargs.get('man_grad'))
        if new_values.shape + data.shape != deriv.shape:
            raise Exception('Manual derivative does not have correct shape.')
    elif kwargs.get('num_grad') is True:
        raise Exception('Multi mode currently not supported for numerical derivative')
    else:
        deriv = jacobian(func)(values, **kwargs)

    final_result = np.zeros(new_values.shape, dtype=object)

    d_extracted = {}
    for name in new_names:
        d_extracted[name] = []
        for i_dat, dat in enumerate(data):
            ens_length = dat.ravel()[0].shape[name]
            d_extracted[name].append(np.array([o.deltas[name] for o in dat.reshape(np.prod(dat.shape))]).reshape(dat.shape + (ens_length, )))

    for i_val, new_val in np.ndenumerate(new_values):
        new_deltas = {}
        for name in new_names:
            ens_length = d_extracted[name][0].shape[-1]
            new_deltas[name] = np.zeros(ens_length)
            for i_dat, dat in enumerate(d_extracted[name]):
                new_deltas[name] += np.tensordot(deriv[i_val + (i_dat, )], dat)

        new_samples = []
        new_means = []
        for name in new_names:
            new_samples.append(new_deltas[name])
            new_means.append(new_r_values[name][i_val])

        final_result[i_val] = Obs(new_samples, new_names, means=new_means)
        final_result[i_val]._value = new_val

    return final_result


def matmul(x1, x2):
    if isinstance(x1[0, 0], CObs) or isinstance(x2[0, 0], CObs):
        Lr, Li = np.vectorize(lambda x: (np.real(x), np.imag(x)))(x1)
        Rr, Ri = np.vectorize(lambda x: (np.real(x), np.imag(x)))(x2)
        Nr = derived_array(lambda x: x[0] @ x[2] - x[1] @ x[3], [Lr, Li, Rr, Ri])
        Ni = derived_array(lambda x: x[0] @ x[3] + x[1] @ x[2], [Lr, Li, Rr, Ri])
        res = np.empty_like(Nr)
        for (n, m), entry in np.ndenumerate(Nr):
            res[n, m] = CObs(Nr[n, m], Ni[n, m])
        return res
    else:
        return derived_array(lambda x: x[0] @ x[1], [x1, x2])


def inv(x):
    return mat_mat_op(anp.linalg.inv, x)


def cholesky(x):
    return mat_mat_op(anp.linalg.cholesky, x)


def scalar_mat_op(op, obs, **kwargs):
    """Computes the matrix to scalar operation op to a given matrix of Obs."""
    def _mat(x, **kwargs):
        dim = int(np.sqrt(len(x)))
        if np.sqrt(len(x)) != dim:
            raise Exception('Input has to have dim**2 entries')

        mat = []
        for i in range(dim):
            row = []
            for j in range(dim):
                row.append(x[j + dim * i])
            mat.append(row)

        return op(anp.array(mat))

    if isinstance(obs, np.ndarray):
        raveled_obs = (1 * (obs.ravel())).tolist()
    elif isinstance(obs, list):
        raveled_obs = obs
    else:
        raise TypeError('Unproper type of input.')
    return derived_observable(_mat, raveled_obs, **kwargs)


def mat_mat_op(op, obs, **kwargs):
    """Computes the matrix to matrix operation op to a given matrix of Obs."""
    # Use real representation to calculate matrix operations for complex matrices
    if isinstance(obs.ravel()[0], CObs):
        A = np.empty_like(obs)
        B = np.empty_like(obs)
        for (n, m), entry in np.ndenumerate(obs):
            if hasattr(entry, 'real') and hasattr(entry, 'imag'):
                A[n, m] = entry.real
                B[n, m] = entry.imag
            else:
                A[n, m] = entry
                B[n, m] = 0.0
        big_matrix = np.block([[A, -B], [B, A]])
        if kwargs.get('num_grad') is True:
            op_big_matrix = _num_diff_mat_mat_op(op, big_matrix, **kwargs)
        else:
            op_big_matrix = derived_array(lambda x, **kwargs: op(x), [big_matrix])[0]
        dim = op_big_matrix.shape[0]
        op_A = op_big_matrix[0: dim // 2, 0: dim // 2]
        op_B = op_big_matrix[dim // 2:, 0: dim // 2]
        res = np.empty_like(op_A)
        for (n, m), entry in np.ndenumerate(op_A):
            res[n, m] = CObs(op_A[n, m], op_B[n, m])
        return res
    else:
        if kwargs.get('num_grad') is True:
            return _num_diff_mat_mat_op(op, obs, **kwargs)
        return derived_array(lambda x, **kwargs: op(x), [obs])[0]


def eigh(obs, **kwargs):
    """Computes the eigenvalues and eigenvectors of a given hermitian matrix of Obs according to np.linalg.eigh."""
    if kwargs.get('num_grad') is True:
        return _num_diff_eigh(obs, **kwargs)
    w = derived_observable(lambda x, **kwargs: anp.linalg.eigh(x)[0], obs)
    v = derived_observable(lambda x, **kwargs: anp.linalg.eigh(x)[1], obs)
    return w, v


def eig(obs, **kwargs):
    """Computes the eigenvalues of a given matrix of Obs according to np.linalg.eig."""
    if kwargs.get('num_grad') is True:
        return _num_diff_eig(obs, **kwargs)
        # Note: Automatic differentiation of eig is implemented in the git of autograd
        # but not yet released to PyPi (1.3)
    w = derived_observable(lambda x, **kwargs: anp.real(anp.linalg.eig(x)[0]), obs)
    return w


def pinv(obs, **kwargs):
    """Computes the Moore-Penrose pseudoinverse of a matrix of Obs."""
    if kwargs.get('num_grad') is True:
        return _num_diff_pinv(obs, **kwargs)
    return derived_observable(lambda x, **kwargs: anp.linalg.pinv(x), obs)


def svd(obs, **kwargs):
    """Computes the singular value decomposition of a matrix of Obs."""
    if kwargs.get('num_grad') is True:
        return _num_diff_svd(obs, **kwargs)
    u = derived_observable(lambda x, **kwargs: anp.linalg.svd(x, full_matrices=False)[0], obs)
    s = derived_observable(lambda x, **kwargs: anp.linalg.svd(x, full_matrices=False)[1], obs)
    vh = derived_observable(lambda x, **kwargs: anp.linalg.svd(x, full_matrices=False)[2], obs)
    return (u, s, vh)


def slog_det(obs, **kwargs):
    """Computes the determinant of a matrix of Obs via np.linalg.slogdet."""
    def _mat(x):
        dim = int(np.sqrt(len(x)))
        if np.sqrt(len(x)) != dim:
            raise Exception('Input has to have dim**2 entries')

        mat = []
        for i in range(dim):
            row = []
            for j in range(dim):
                row.append(x[j + dim * i])
            mat.append(row)

        (sign, logdet) = anp.linalg.slogdet(np.array(mat))
        return sign * anp.exp(logdet)

    if isinstance(obs, np.ndarray):
        return derived_observable(_mat, (1 * (obs.ravel())).tolist(), **kwargs)
    elif isinstance(obs, list):
        return derived_observable(_mat, obs, **kwargs)
    else:
        raise TypeError('Unproper type of input.')


# Variants for numerical differentiation

def _num_diff_mat_mat_op(op, obs, **kwargs):
    """Computes the matrix to matrix operation op to a given matrix of Obs elementwise
       which is suitable for numerical differentiation."""
    def _mat(x, **kwargs):
        dim = int(np.sqrt(len(x)))
        if np.sqrt(len(x)) != dim:
            raise Exception('Input has to have dim**2 entries')

        mat = []
        for i in range(dim):
            row = []
            for j in range(dim):
                row.append(x[j + dim * i])
            mat.append(row)

        return op(np.array(mat))[kwargs.get('i')][kwargs.get('j')]

    if isinstance(obs, np.ndarray):
        raveled_obs = (1 * (obs.ravel())).tolist()
    elif isinstance(obs, list):
        raveled_obs = obs
    else:
        raise TypeError('Unproper type of input.')

    dim = int(np.sqrt(len(raveled_obs)))

    res_mat = []
    for i in range(dim):
        row = []
        for j in range(dim):
            row.append(derived_observable(_mat, raveled_obs, i=i, j=j, **kwargs))
        res_mat.append(row)

    return np.array(res_mat) @ np.identity(dim)


def _num_diff_eigh(obs, **kwargs):
    """Computes the eigenvalues and eigenvectors of a given hermitian matrix of Obs according to np.linalg.eigh
       elementwise which is suitable for numerical differentiation."""
    def _mat(x, **kwargs):
        dim = int(np.sqrt(len(x)))
        if np.sqrt(len(x)) != dim:
            raise Exception('Input has to have dim**2 entries')

        mat = []
        for i in range(dim):
            row = []
            for j in range(dim):
                row.append(x[j + dim * i])
            mat.append(row)

        n = kwargs.get('n')
        res = np.linalg.eigh(np.array(mat))[n]

        if n == 0:
            return res[kwargs.get('i')]
        else:
            return res[kwargs.get('i')][kwargs.get('j')]

    if isinstance(obs, np.ndarray):
        raveled_obs = (1 * (obs.ravel())).tolist()
    elif isinstance(obs, list):
        raveled_obs = obs
    else:
        raise TypeError('Unproper type of input.')

    dim = int(np.sqrt(len(raveled_obs)))

    res_vec = []
    for i in range(dim):
        res_vec.append(derived_observable(_mat, raveled_obs, n=0, i=i, **kwargs))

    res_mat = []
    for i in range(dim):
        row = []
        for j in range(dim):
            row.append(derived_observable(_mat, raveled_obs, n=1, i=i, j=j, **kwargs))
        res_mat.append(row)

    return (np.array(res_vec) @ np.identity(dim), np.array(res_mat) @ np.identity(dim))


def _num_diff_eig(obs, **kwargs):
    """Computes the eigenvalues of a given matrix of Obs according to np.linalg.eig
       elementwise which is suitable for numerical differentiation."""
    def _mat(x, **kwargs):
        dim = int(np.sqrt(len(x)))
        if np.sqrt(len(x)) != dim:
            raise Exception('Input has to have dim**2 entries')

        mat = []
        for i in range(dim):
            row = []
            for j in range(dim):
                row.append(x[j + dim * i])
            mat.append(row)

        n = kwargs.get('n')
        res = np.linalg.eig(np.array(mat))[n]

        if n == 0:
            # Discard imaginary part of eigenvalue here
            return np.real(res[kwargs.get('i')])
        else:
            return res[kwargs.get('i')][kwargs.get('j')]

    if isinstance(obs, np.ndarray):
        raveled_obs = (1 * (obs.ravel())).tolist()
    elif isinstance(obs, list):
        raveled_obs = obs
    else:
        raise TypeError('Unproper type of input.')

    dim = int(np.sqrt(len(raveled_obs)))

    res_vec = []
    for i in range(dim):
        # Note: Automatic differentiation of eig is implemented in the git of autograd
        # but not yet released to PyPi (1.3)
        res_vec.append(derived_observable(_mat, raveled_obs, n=0, i=i, **kwargs))

    return np.array(res_vec) @ np.identity(dim)


def _num_diff_pinv(obs, **kwargs):
    """Computes the Moore-Penrose pseudoinverse of a matrix of Obs elementwise which is suitable
       for numerical differentiation."""
    def _mat(x, **kwargs):
        shape = kwargs.get('shape')

        mat = []
        for i in range(shape[0]):
            row = []
            for j in range(shape[1]):
                row.append(x[j + shape[1] * i])
            mat.append(row)

        return np.linalg.pinv(np.array(mat))[kwargs.get('i')][kwargs.get('j')]

    if isinstance(obs, np.ndarray):
        shape = obs.shape
        raveled_obs = (1 * (obs.ravel())).tolist()
    else:
        raise TypeError('Unproper type of input.')

    res_mat = []
    for i in range(shape[1]):
        row = []
        for j in range(shape[0]):
            row.append(derived_observable(_mat, raveled_obs, shape=shape, i=i, j=j, **kwargs))
        res_mat.append(row)

    return np.array(res_mat) @ np.identity(shape[0])


def _num_diff_svd(obs, **kwargs):
    """Computes the singular value decomposition of a matrix of Obs elementwise which
       is suitable for numerical differentiation."""
    def _mat(x, **kwargs):
        shape = kwargs.get('shape')

        mat = []
        for i in range(shape[0]):
            row = []
            for j in range(shape[1]):
                row.append(x[j + shape[1] * i])
            mat.append(row)

        res = np.linalg.svd(np.array(mat), full_matrices=False)

        if kwargs.get('n') == 1:
            return res[1][kwargs.get('i')]
        else:
            return res[kwargs.get('n')][kwargs.get('i')][kwargs.get('j')]

    if isinstance(obs, np.ndarray):
        shape = obs.shape
        raveled_obs = (1 * (obs.ravel())).tolist()
    else:
        raise TypeError('Unproper type of input.')

    mid_index = min(shape[0], shape[1])

    res_mat0 = []
    for i in range(shape[0]):
        row = []
        for j in range(mid_index):
            row.append(derived_observable(_mat, raveled_obs, shape=shape, n=0, i=i, j=j, **kwargs))
        res_mat0.append(row)

    res_mat1 = []
    for i in range(mid_index):
        res_mat1.append(derived_observable(_mat, raveled_obs, shape=shape, n=1, i=i, **kwargs))

    res_mat2 = []
    for i in range(mid_index):
        row = []
        for j in range(shape[1]):
            row.append(derived_observable(_mat, raveled_obs, shape=shape, n=2, i=i, j=j, **kwargs))
        res_mat2.append(row)

    return (np.array(res_mat0) @ np.identity(mid_index), np.array(res_mat1) @ np.identity(mid_index), np.array(res_mat2) @ np.identity(shape[1]))


_dot = partial(anp.einsum, '...ij,...jk->...ik')


# batched diag
def _diag(a):
    return anp.eye(a.shape[-1]) * a


# batched diagonal, similar to matrix_diag in tensorflow
def _matrix_diag(a):
    reps = anp.array(a.shape)
    reps[:-1] = 1
    reps[-1] = a.shape[-1]
    newshape = list(a.shape) + [a.shape[-1]]
    return _diag(anp.tile(a, reps).reshape(newshape))

# https://arxiv.org/pdf/1701.00392.pdf Eq(4.77)
# Note the formula from Sec3.1 in https://people.maths.ox.ac.uk/gilesm/files/NA-08-01.pdf is incomplete


def grad_eig(ans, x):
    """Gradient of a general square (complex valued) matrix"""
    e, u = ans  # eigenvalues as 1d array, eigenvectors in columns
    n = e.shape[-1]

    def vjp(g):
        ge, gu = g
        ge = _matrix_diag(ge)
        f = 1 / (e[..., anp.newaxis, :] - e[..., :, anp.newaxis] + 1.e-20)
        f -= _diag(f)
        ut = anp.swapaxes(u, -1, -2)
        r1 = f * _dot(ut, gu)
        r2 = -f * (_dot(_dot(ut, anp.conj(u)), anp.real(_dot(ut, gu)) * anp.eye(n)))
        r = _dot(_dot(anp.linalg.inv(ut), ge + r1 + r2), ut)
        if not anp.iscomplexobj(x):
            r = anp.real(r)
            # the derivative is still complex for real input (imaginary delta is allowed), real output
            # but the derivative should be real in real input case when imaginary delta is forbidden
        return r
    return vjp


defvjp(anp.linalg.eig, grad_eig)
# End of the code block from autograd.master
