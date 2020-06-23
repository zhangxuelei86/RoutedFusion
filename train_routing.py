import torch
import argparse
import datetime

from tqdm import tqdm

from utils.loading import load_config_from_yaml
from utils.setup import *

from utils.loss import RoutingLoss
from modules.routing import ConfidenceRouting

def arg_parser():

    parser = argparse.ArgumentParser()

    parser.add_argument('--config', required=False)
    parser.add_argument('--device', default='cpu', required=False)

    args = parser.parse_args()

    return vars(args)


def train(args, config):

    if args['device'] == 'cpu':
        device = torch.device("cpu")
    elif args['device'] == 'gpu':
        device = torch.device('cuda:0')

    config.TIMESTAMP = datetime.datetime.now().strftime('%y%m%d-%H%M%S')


    workspace = get_workspace(config)

    # get train dataset
    train_data_config = get_data_config(config, mode='train')
    train_dataset = get_data(config.DATA.dataset, train_data_config)
    train_loader = torch.utils.data.DataLoader(train_dataset, config.TRAINING.train_batch_size)

    # get val dataset
    val_data_config = get_data_config(config, mode='val')
    val_dataset = get_data(config.DATA.dataset, val_data_config)
    val_loader = torch.utils.data.DataLoader(val_dataset,
                                             config.TRAINING.val_batch_size)

    # define model
    model = ConfidenceRouting(Cin=config.MODEL.n_input_channels,
                              F=config.MODEL.contraction,
                              Cout=config.MODEL.n_output_channels,
                              depth=config.MODEL.depth,
                              batchnorms=config.MODEL.normalization)
    model = model.to(device)

    # define loss function
    criterion = RoutingLoss(config)
    criterion = criterion.to(device)

    # define optimizer
    optimizer = torch.optim.RMSprop(model.parameters(),
                                    config.OPTIMIZATION.lr,
                                    config.OPTIMIZATION.rho,
                                    config.OPTIMIZATION.eps,
                                    momentum=config.OPTIMIZATION.momentum,
                                    weight_decay=config.OPTIMIZATION.weight_decay)

    n_train_batches = int(len(train_dataset) / config.TRAINING.train_batch_size)
    n_val_batches = int(len(val_dataset) / config.TRAINING.val_batch_size)

    # # define metrics
    l1_criterion = torch.nn.L1Loss()
    l2_criterion = torch.nn.MSELoss()

    for epoch in range(0, config.TRAINING.n_epochs):

        for i, batch in enumerate(tqdm(train_loader, total=n_train_batches)):

            inputs = batch[config.DATA.input]
            inputs = inputs.unsqueeze_(1)
            inputs = inputs.to(device)

            target = batch[config.DATA.target]
            target = target.to(device)
            target = target.unsqueeze_(1)

            output = model.forward(inputs)

            est = output[:, 0, :, :].unsqueeze_(1)
            unc = output[:, 1, :, :].unsqueeze_(1)

            if config.DATA.dataset == 'modelnet' or config.DATA.dataset == 'shapenet':
                mask = batch['mask'].to(device).unsqueeze_(1)
                gradient_mask = batch['gradient_mask'].to(device).unsqueeze_(1)

                est = torch.where(mask == 0., torch.zeros_like(est), est)
                unc = torch.where(mask == 0., torch.zeros_like(unc), unc)
                target = torch.where(mask == 0., torch.zeros_like(target), target)

            else:
                gradient_mask = None

            loss = criterion.forward(est, unc, target, gradient_mask)
            loss.backward()

            if i % config.OPTIMIZATION.accumulation_steps == 0:
                optimizer.step()
                optimizer.zero_grad()

        val_loss_t = 0.
        val_loss_l1 = 0.
        val_loss_l2 = 0.

        val_loss_best = np.infty

        for i, batch in enumerate(tqdm(val_loader, total=n_val_batches)):

            inputs = batch[config.DATA.input]
            inputs = inputs.unsqueeze_(1)
            inputs = inputs.to(device)

            target = batch[config.DATA.target]
            target = target.to(device)
            target = target.unsqueeze_(1)

            output = model.forward(inputs)

            est = output[:, 0, :, :].unsqueeze_(1)
            unc = output[:, 1, :, :].unsqueeze_(1)

            if config.DATA.dataset == 'modelnet' or config.DATA.dataset == 'shapenet':
                mask = batch['mask'].to(device).unsqueeze_(1)
                gradient_mask = batch['gradient_mask'].to(device).unsqueeze_(1)

                est = torch.where(mask == 0., torch.zeros_like(est), est)
                unc = torch.where(mask == 0., torch.zeros_like(unc), unc)
                target = torch.where(mask == 0., torch.zeros_like(target),
                                     target)

            else:
                gradient_mask = None

            loss_t = criterion.forward(est, unc, target, gradient_mask)
            loss_l1 = l1_criterion.forward(est, target)
            loss_l2 = l2_criterion.forward(est, target)

            val_loss_t += loss_t.item()
            val_loss_l1 += loss_l1.item()
            val_loss_l2 += loss_l2.item()

        val_loss_t /= n_val_batches
        val_loss_l1 /= n_val_batches
        val_loss_l2 /= n_val_batches

        workspace.log('Epoch {} Loss {}'.format(epoch, val_loss_t))
        workspace.log('Epoch {} L1 Loss {}'.format(epoch, val_loss_l1))
        workspace.log('Epoch {} L2 Loss {}'.format(epoch, val_loss_l2))

        # define model state for storing
        model_state = {'epoch': epoch,
                       'state_dict': model.state_dict(),
                       'optim_dict': optimizer.state_dict()}

        if val_loss_t <= val_loss_best:
            val_loss_best = val_loss_t
            workspace.log('Found new best model with loss {} at epoch {}'.format(val_loss_best, epoch), mode='val')
            workspace.save_model_state(model_state, is_best=True)
        else:
            workspace.save_model_state(model_state)


if __name__ == '__main__':

    # get arguments
    args = arg_parser()

    # get configs
    config = load_config_from_yaml(args['config'])

    # train
    train(args, config)

