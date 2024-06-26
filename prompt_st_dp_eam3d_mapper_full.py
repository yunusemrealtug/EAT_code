import matplotlib

matplotlib.use('Agg')

import os, sys
import yaml
from argparse import ArgumentParser
from time import gmtime, strftime
from shutil import copy

# from frames_dataset_transformer25 import FramesWavsDatasetMEL25VoxWoTBatch  as FramesWavsDatasetMEL25
# from frames_dataset_transformer25 import FramesWavsDatasetMEL25VoxBoxQG2ImgPromptNovelStyle as FramesWavsDatasetMEL25
from frames_dataset_transformer25 import FramesWavsDatasetMEL25VoxBoxQG2ImgPromptNovelStyle2 as FramesWavsDatasetMEL25

from modules.generator import OcclusionAwareGenerator, OcclusionAwareSPADEGeneratorEam, OcclusionAwareSPADEGenerator
from modules.discriminator import MultiScaleDiscriminator
from modules.keypoint_detector import KPDetector, HEEstimator
from modules.transformer import Audio2kpTransformerBBoxQDeepPrompt as Audio2kpTransformer
from modules.prompt import EmotionalDeformationTransformer
from modules.prompt import  Mapper

import torch

# from train_transformer import train_batch_prompt_adain3d_sidetuning as train
from train_transformer import train_batch_prompt_mapper3 as train
# 'very interesting face'
# text = [    
#             # 'little smile with nose wrinkled',
#             # 'little smile with eyebrow frown',
#             "The woman is crying hard with tears",
#             # 'slight forced smile',
#             # '',
#             # 'talking in a very painful expression with nose wrinkled.',
#             # 'The person looks painful with lips tight',
#             # 'a painful expression with slightly puckered',
#             # 'Brows furrowed, eyes wide, lips tightened and pressed together',
#             'The woman is with smile.']
# text=[ 'The woman is crying with smile.']
# text=[ 'mouth shrug.']
# text=[ 'The woman is with smile and extremely raised eyebrown.']
# text=[ 'The woman is angry with eyes open.']
# text=[ 'Aha.']
# text=[ 'Angry and Surprised']
# text = [    
#             'The girl is winking',
#             'The woman is with little smile.']
# text = [    'She is talking with her nose wrinkled.']
# text = [ 'She is talking while feeling very painful.']
# text = ['She is talking with a fierce expression.']
text = ['She is talking while crying hard.']
# text = [    'She is talking with her nose wrinkled.']
# text = [    'The woman is smiling and shocked']
print(text)

if __name__ == "__main__":
    
    if sys.version_info[0] < 3:
        raise Exception("You must use Python 3 or higher. Recommended version is Python 3.7")

    parser = ArgumentParser()
    parser.add_argument("--config", default="config/vox-transformer.yaml", help="path to config")
    parser.add_argument("--mode", default="train", choices=["train",])
    parser.add_argument("--gen", default="spade", choices=["original", "spade"])
    parser.add_argument("--log_dir", default='./output/', help="path to log into")
    parser.add_argument("--checkpoint", default='./00000189-checkpoint.pth.tar', help="path to checkpoint to restore")
    parser.add_argument("--device_ids", default="0, 1, 2, 3, 4, 5, 6, 7", type=lambda x: list(map(int, x.split(','))),
                        help="Names of the devices comma separated.")
    parser.add_argument("--verbose", dest="verbose", action="store_true", help="Print model architecture")
    parser.set_defaults(verbose=False)

    opt = parser.parse_args()
    with open(opt.config) as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
    
    torch.manual_seed(42)
    torch.cuda.manual_seed(42)
    torch.cuda.manual_seed_all(42)

    # log dir when checkpoint is set
    # if opt.checkpoint is not None:
    #     log_dir = os.path.join(*os.path.split(opt.checkpoint)[:-1])
    # else:
    log_dir = os.path.join(opt.log_dir, os.path.basename(opt.config).split('.')[0])
    log_dir += ' ' + strftime("%d_%m_%y_%H.%M.%S", gmtime())


    if opt.gen == 'original':
        generator = OcclusionAwareGenerator(**config['model_params']['generator_params'],
                                            **config['model_params']['common_params'])
    elif opt.gen == 'spade':
        generator = OcclusionAwareSPADEGeneratorEam(**config['model_params']['generator_params'],
                                                 **config['model_params']['common_params'])

    for param in generator.parameters():
        param.requires_grad = False
    gsd=generator.state_dict(keep_vars=True)
    for name in gsd:
        if 'ea1' in name or 'ea2' in name or 'ea3d' in name:
            gsd[name].requires_grad=True
            print('set ea3d trainable', name, gsd[name].requires_grad)

    if torch.cuda.is_available():
        print('cuda is available')
        generator.to(opt.device_ids[0])
    if opt.verbose:
        print(generator)

    # discriminator = MultiScaleDiscriminator(**config['model_params']['discriminator_params'],
    #                                         **config['model_params']['common_params'])
    # if torch.cuda.is_available():
    #     discriminator.to(opt.device_ids[0])
    # if opt.verbose:
    #     print(discriminator)

    mapper = Mapper()

    if torch.cuda.is_available():
        mapper.to(opt.device_ids[0])

    kp_detector = KPDetector(**config['model_params']['kp_detector_params'],
                             **config['model_params']['common_params'])

    if torch.cuda.is_available():
        kp_detector.to(opt.device_ids[0])

    if opt.verbose:
        print(kp_detector)

    audio2kptransformer = Audio2kpTransformer(**config['model_params']['audio2kp_params'], face_ea=True)
    # audio2kptransformer = Audio2kpTransformer(**config['model_params']['audio2kp_params'])

    if torch.cuda.is_available():
        audio2kptransformer.to(opt.device_ids[0])

    for param in audio2kptransformer.parameters():
        param.requires_grad = False
    audencoder=audio2kptransformer.audioencoder.state_dict(keep_vars=True)
    for name in audencoder:
        if 'decode' in name and 'norm' in name:
            audencoder[name].requires_grad=True
            print('set eam trainable', name, audencoder[name].requires_grad)
    fea = audio2kptransformer.fea.state_dict(keep_vars=True)
    print('------------------------------------------')
    for name in fea:
        fea[name].requires_grad=True
        print('set se trainable', name, fea[name].requires_grad)

    sidetuning = EmotionalDeformationTransformer(**config['model_params']['audio2kp_params'])

    if torch.cuda.is_available():
        sidetuning.to(opt.device_ids[0])

    dataset = FramesWavsDatasetMEL25(is_train=(opt.mode == 'train'), **config['dataset_params'])

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    if not os.path.exists(os.path.join(log_dir, os.path.basename(opt.config))):
        copy(opt.config, log_dir)

    if opt.mode == 'train':
        print("Training...")
        train(text, config, generator, None, kp_detector, audio2kptransformer, mapper, sidetuning, opt.checkpoint, log_dir, dataset, opt.device_ids)
