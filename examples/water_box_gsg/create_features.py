import sys
import os
import os.path as osp
import pickle as pkl
import numpy as np

import torch
from flexibletopology.mlmodels.gsg import GSG

INPUTS_PATH = 'inputs'
DATASET_PATH = osp.join(INPUTS_PATH,
                        'openchem_mols.pkl')


SM_OPERATORS = (True, True, False)
MAX_WAVELET_SCALE = 4
RADIAL_CUTOFF = 0.2

def save_data(dataset_path, idx_target):


    with open(dataset_path, 'rb') as pklf:
        data = pkl.load(pklf)


    target_signals = np.copy(data['cgenff_signals_notype'][idx_target])

    # convert to nm
    target_coords = np.copy(data['coords'][idx_target]) / 10


    AniGSG_model = GSG(max_wavelet_scale=MAX_WAVELET_SCALE,
                              radial_cutoff=RADIAL_CUTOFF,
                              sm_operators=SM_OPERATORS)


    target_coords = torch.from_numpy(target_coords)
    target_coords.requires_grad = True

    target_signals = torch.from_numpy(target_signals)
    target_signals.requires_grad = True

    target_features = AniGSG_model(target_coords, target_signals)


    data = {'target_coords': target_coords.detach().numpy(),
            'target_signals': target_signals.detach().numpy(),
            'target_features': target_features.detach().numpy()}


    #Save data into a pickle file
    sm_operators_str = ''.join([str(int(flag)) for flag in SM_OPERATORS])
    outfile_name = f'T{idx_target}_W{MAX_WAVELET_SCALE}_{sm_operators_str}_gsg.pkl'
    save_path = os.path.join(INPUTS_PATH, outfile_name)

    with open(save_path, 'wb') as wfile:
        pkl.dump(data, wfile)

    print("saved the features data")


if __name__=='__main__':

    if len(sys.argv) != 2 or sys.argv[1] == "-h" or sys.argv[1] == "--help":
        print("arguments: idx_target")
        exit()
    else:
        idx_target = int(sys.argv[1])
        save_data(DATASET_PATH, idx_target)
